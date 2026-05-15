"""Orchestrate per-filing extraction: edgartools for P&L + LLM for segments.

Flow:
1. Parse filing with edgartools (TenK / TenQ / TwentyF).
2a. Financial sector  → sector_handlers.financials.pnl_from_financial_filing()
2b. Pharma sector     → sector_handlers.pharma.pnl_from_pharma_filing()
2c. Standard sector   → sector_handlers.standard.pnl_from_standard_filing()
3. Detect geo-only XBRL → bypass XBRL, use LLM from Revenue/MD&A note.
4. Find Segment note; feed to LLM if XBRL coverage < 70%.
5. Mandatory LLM fallback from raw filing text when note_text is empty.
6. Rescale LLM segments if they are wildly off vs edgartools total.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import (
    XBRL_COVERAGE_MIN, SEGMENT_MIN_PCT, SEGMENT_MIN_VALUE,
    SEGMENT_RESCALE_MIN, SEGMENT_RESCALE_MAX, REVENUE_WARN_DIFF,
    BANK_NII_MIN_PCT, BANK_PROVISION_MAX_PCT, INSURANCE_CLAIMS_MAX_PCT,
)
from src.extraction.models import SegmentData, SegmentValue, FilingRecord, compute_confidence
from src.extraction.llm_extractor import (
    extract_segments, extract_segments_from_yahoo_summary,
)
from src.ingestion.ticker_loader import TickerInfo
from src.ingestion.edgar_client import (
    get_filing_obj, get_segment_note_text,
    segments_from_xbrl_dimensions,
)


# Geographic term set and skip-words used to detect purely geographic segment names.
_GEO_KEYWORDS = frozenset({
    "americas", "north america", "latin america", "south america",
    "europe", "emea", "middle east", "africa",
    "asia", "asia pacific", "asia-pacific", "apac",
    "rest of asia pacific", "rest of asia",
    "greater china", "china", "japan",
    "u.s.", "non-u.s.", "non-us", "domestic", "international",
    "united states", "outside u.s.", "outside the u.s.",
    "other regions", "rest of world", "worldwide",
})
_GEO_SKIP_WORDS = frozenset({
    "and", "or", "of", "the", "rest", "other", "greater", "excluding",
    "segment", "segments", "region", "regions",
})


def _is_pure_geo_name(name: str) -> bool:
    """True only when a segment name is a purely geographic label.

    'Americas', 'U.S.', 'Greater China', 'U.S. Revenue' → True.
    'Walmart U.S.', 'International Operated Markets' → False.
    """
    n = name.replace("\xa0", " ").lower().strip()
    # Strip revenue/sales suffixes so "U.S. Revenue" and "Europe Revenue" are detected
    for sfx in (" revenue", " revenues", " sales", " net sales"):
        if n.endswith(sfx):
            n = n[:-len(sfx)].strip()
            break
    remaining = n
    for kw in sorted(_GEO_KEYWORDS, key=len, reverse=True):
        remaining = remaining.replace(kw, " ")
    words = {w.strip("().,&-'\"/") for w in remaining.split()} - _GEO_SKIP_WORDS - {""}
    return len(words) == 0


def _is_geo_only(segments: "List") -> bool:
    if not segments:
        return False
    # Segments tagged by edgar_client as coming from StatementGeographicalAxis
    if all(getattr(s, "concept", "") == "xbrl_geo_axis" for s in segments):
        return True
    return all(_is_pure_geo_name(s.segment_name) for s in segments)


def _strip_geo_from_llm(segs: "List") -> "List":
    """Remove geo-named segments from LLM output when non-geo items also present.

    LLMs reading Revenue notes sometimes return BOTH a product-type breakdown
    AND a geographic breakdown (e.g. LLY: Net Product Revenue + U.S. Revenue +
    Europe Revenue + ...).  When non-geo items exist, drop the geo ones so the
    coverage comparison with XBRL is apples-to-apples.
    """
    if not segs:
        return segs
    non_geo = [s for s in segs if not _is_pure_geo_name(s.segment_name)]
    if non_geo and len(non_geo) < len(segs):
        return non_geo
    return segs


def extract_for_filing(
    filing:      FilingRecord,
    ticker_info: TickerInfo,
    revenue_map: Optional[Dict[str, Optional[float]]] = None,
    **_ignored,
) -> SegmentData:
    """Extract SegmentData for one filing using edgartools + LLM."""
    ticker = ticker_info.ticker
    period = filing.period
    sector = ticker_info.sector

    known_rev = None
    if revenue_map:
        known_rev = revenue_map.get(period) or revenue_map.get(f"FY{filing.fiscal_year}")

    # 1) Parse filing via edgartools
    obj = get_filing_obj(filing)

    # 2) P&L: route to the appropriate sector handler.
    _sector_lower = (sector or "").lower()
    _is_financial = _sector_lower == "financial"
    _effective_sector = _sector_lower  # may change on fallback

    if _is_financial:
        from src.extraction.sector_handlers.financials import (
            pnl_from_financial_filing, has_bank_indicators,
        )
        pnl = pnl_from_financial_filing(obj)
        _check_rev = pnl.get("total_revenue")
        if _check_rev and not has_bank_indicators(pnl, _check_rev):
            from src.extraction.sector_handlers.standard import pnl_from_standard_filing
            pnl = pnl_from_standard_filing(obj)
            _effective_sector = "standard"
            _is_financial = False
    elif _sector_lower == "pharma":
        from src.extraction.sector_handlers.pharma import (
            pnl_from_pharma_filing, has_pharma_indicators,
        )
        pnl = pnl_from_pharma_filing(obj)
        _check_rev = pnl.get("total_revenue")
        if _check_rev and not has_pharma_indicators(pnl, _check_rev):
            from src.extraction.sector_handlers.standard import pnl_from_standard_filing
            pnl = pnl_from_standard_filing(obj)
            _effective_sector = "standard"
    else:
        from src.extraction.sector_handlers.standard import pnl_from_standard_filing
        pnl = pnl_from_standard_filing(obj)

    # Companyfacts fallback when edgartools returns no total_revenue
    if pnl.get("total_revenue") is None and obj is not None:
        try:
            from src.ingestion.edgar_client import pnl_from_companyfacts
            cf_pnl = pnl_from_companyfacts(ticker)
            if cf_pnl.get("total_revenue"):
                for k, v in cf_pnl.items():
                    if v is not None and pnl.get(k) is None:
                        pnl[k] = v
        except Exception:
            pass

    total_rev = pnl.get("total_revenue") or known_rev

    # 3) Segment extraction: XBRL-first unless geo-only.
    # Financial sector uses broader concept hints (bank income ≠ standard revenue tags).
    segments: List[SegmentValue] = segments_from_xbrl_dimensions(
        obj, total_revenue=total_rev, is_financial=_is_financial, ticker=ticker
    )
    method = "xbrl" if segments else "edgar"

    # Drop sub-line segments with negligible values (< 0.5% of total revenue or < $50M)
    # This removes artifacts like "Interchange and merchant services fees - WIM: $0B" in WFC
    if segments and total_rev:
        segments = [s for s in segments if s.value and s.value >= max(total_rev * SEGMENT_MIN_PCT, SEGMENT_MIN_VALUE)]
    elif segments:
        segments = [s for s in segments if s.value and s.value >= SEGMENT_MIN_VALUE]

    # Detect geo-only XBRL (e.g. AAPL geographic axis, BAC U.S./Non-U.S.)
    geo_only = _is_geo_only(segments)

    xbrl_coverage = 0.0
    if segments and total_rev and not geo_only:
        xbrl_coverage = sum(s.value for s in segments if s.value) / total_rev

    use_llm = geo_only or (not segments) or (xbrl_coverage < XBRL_COVERAGE_MIN)

    if use_llm:
        if geo_only:
            # For geo-only companies try product-level sources first
            note_text = _get_product_note_text(obj)
        else:
            note_text = get_segment_note_text(obj)

        if note_text:
            llm_segs = extract_segments(
                note_text       = note_text,
                ticker          = ticker,
                period          = period,
                sector          = sector,
                known_total_rev = total_rev,
            )
            if llm_segs:
                llm_segs = _strip_geo_from_llm(llm_segs)
                llm_sum = sum(s.value for s in llm_segs)
                llm_cov = llm_sum / total_rev if total_rev else 0
                if geo_only or not segments or llm_cov > xbrl_coverage:
                    segments = llm_segs
                    method   = "edgar+llm"
                    xbrl_coverage = llm_cov

        # For geo-only: if product note yielded no product segments, also try the
        # segment note (e.g. CVX/XOM where the "Revenue" policy note has no numbers)
        if geo_only and (not segments or _is_geo_only(segments)):
            seg_note = get_segment_note_text(obj)
            if seg_note:
                llm_segs = extract_segments(
                    note_text       = seg_note,
                    ticker          = ticker,
                    period          = period,
                    sector          = sector,
                    known_total_rev = total_rev,
                )
                if llm_segs:
                    llm_segs = _strip_geo_from_llm(llm_segs)
                    llm_cov = sum(s.value for s in llm_segs) / total_rev if total_rev else 0
                    segments = llm_segs
                    method   = "edgar+llm"
                    xbrl_coverage = llm_cov

        # If still no segments from any note, try raw filing text as last resort
        if not segments:
            raw_text = _get_raw_filing_text(obj)
            if raw_text:
                llm_segs = extract_segments(
                    note_text       = raw_text,
                    ticker          = ticker,
                    period          = period,
                    sector          = sector,
                    known_total_rev = total_rev,
                )
                if llm_segs:
                    segments = llm_segs
                    method   = "edgar+llm_raw"

        # If still geo-only XBRL and LLM didn't produce product segments, discard geo
        if geo_only and segments and _is_geo_only(segments):
            segments = []

    sd = _build_segment_data(
        ticker    = ticker,
        period    = period,
        is_annual = filing.is_annual,
        fy        = filing.fiscal_year,
        fq        = filing.fiscal_quarter,
        pnl       = pnl,
        segments  = segments,
        method    = method,
    )

    # Record sector fallback so main.py can use the effective sector for aggregation
    if _effective_sector != _sector_lower:
        sd.notes.append(f"SECTOR_FALLBACK:{_effective_sector}")

    # Stash bank expense sub-components in notes for sankey_builder
    expense_detail = pnl.get("expense_detail")
    if expense_detail:
        sd.notes.append(f"EXPENSE_DETAIL:{json.dumps(expense_detail)}")

    # Yahoo revenue sanity check
    if known_rev and sd.total_revenue:
        diff = abs(sd.total_revenue - known_rev) / max(abs(known_rev), 1)
        if diff > REVENUE_WARN_DIFF:
            sd.notes.append(
                f"Warning: total_revenue ({sd.total_revenue:.0f}) differs "
                f"from Yahoo ({known_rev:.0f}) by {diff:.1%}"
            )

    _rescale_segments_if_needed(sd)
    sd.confidence = compute_confidence(sd)
    return sd


def extract_for_yahoo_only(
    ticker_info: TickerInfo,
    period:      str,
    yahoo_data:  Dict,
    is_annual:   bool = True,
    fiscal_year: Optional[int] = None,
) -> SegmentData:
    """INTL_YAHOO path: no SEC filing — use Yahoo data + LLM for segments."""
    ticker   = ticker_info.ticker
    sector   = ticker_info.sector
    fy       = fiscal_year or (int(period[2:6]) if period.startswith("FY") else int(period[:4]))
    usd_rate = yahoo_data.get("usd_rate", 1.0)

    pnl      = _pnl_from_yahoo(yahoo_data, period, is_annual, usd_rate)
    _sector_lower_y  = (sector or "").lower()
    _effective_sector_y = _sector_lower_y

    if _sector_lower_y == "financial":
        from src.extraction.sector_handlers.financials import has_bank_indicators
        _check_rev_y = pnl.get("total_revenue")
        if _check_rev_y and not has_bank_indicators(pnl, _check_rev_y):
            _effective_sector_y = "standard"
    elif _sector_lower_y == "pharma":
        from src.extraction.sector_handlers.pharma import has_pharma_indicators
        _check_rev_y = pnl.get("total_revenue")
        if _check_rev_y and not has_pharma_indicators(pnl, _check_rev_y):
            _effective_sector_y = "standard"

    # No segment extraction for INTL_YAHOO — Yahoo Finance doesn't provide segment
    # breakdowns and LLM inference from business summary text is unreliable.
    segments: list = []

    sd = _build_segment_data(
        ticker=ticker, period=period, is_annual=is_annual,
        fy=fy, fq=None,
        pnl=pnl, segments=segments,
        method="yahoo_only",
    )
    sd.notes.append("Source: Yahoo Finance (no SEC filing available)")
    sd.notes.append("⚠ Limited Segment Detail — aggregate data only")

    if _effective_sector_y != _sector_lower_y:
        sd.notes.append(f"SECTOR_FALLBACK:{_effective_sector_y}")

    # Store bank-specific income components so _build_yahoo_financial_sankey can
    # render NII and Non-Interest Income as separate source nodes.
    bank_nii    = pnl.get("bank_nii")
    bank_nonint = pnl.get("bank_nonint")
    if bank_nii and bank_nii > 0:
        sd.notes.append(f"BANK_NII:{bank_nii}")
    if bank_nonint and bank_nonint > 0:
        sd.notes.append(f"BANK_NONINT:{bank_nonint}")

    sd.confidence = compute_confidence(sd)
    return sd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import json  # noqa: E402 (needed here for expense_detail serialisation)


def _get_product_note_text(filing_obj) -> Optional[str]:
    """
    For geo-only companies (AAPL, BAC, ...) try to find a Revenue note or
    Products & Services note that contains product-level breakdowns.
    Falls back to the standard segment note.
    """
    if filing_obj is None:
        return None
    notes = getattr(filing_obj, "notes", None)
    if not notes:
        return None

    product_keywords = ("revenue", "products and services", "products & services",
                        "net sales", "income statement")
    segment_note = None
    revenue_note = None

    for note in notes:
        label = (getattr(note, "title", "") or str(note) or "").lower()
        if "segment" in label or "business segments" in label or "line of business" in label:
            segment_note = note
        if any(kw in label for kw in product_keywords) and revenue_note is None:
            revenue_note = note

    chosen = revenue_note or segment_note
    if chosen is None:
        return None
    text = getattr(chosen, "text", None)
    return text.strip() if (text and text.strip()) else None


def _get_raw_filing_text(filing_obj) -> Optional[str]:
    """Smart-slice raw filing text around 'segment' keyword as LLM last resort."""
    if filing_obj is None:
        return None
    try:
        text = getattr(filing_obj, "text", None) or ""
        if not text:
            return None
        anchor = text.lower().find("segment")
        if anchor < 0:
            anchor = 0
        start = max(0, anchor - 30_000)
        end   = min(len(text), anchor + 90_000)
        return text[start:end]
    except Exception:
        return None


def _build_segment_data(
    ticker:    str,
    period:    str,
    is_annual: bool,
    fy:        int,
    fq:        Optional[int],
    pnl:       Dict,
    segments:  List[SegmentValue],
    method:    str,
) -> SegmentData:
    return SegmentData(
        ticker            = ticker,
        period            = period,
        is_annual         = is_annual,
        fiscal_year       = fy,
        fiscal_quarter    = fq,
        segments          = segments,
        total_revenue     = pnl.get("total_revenue"),
        gross_profit      = pnl.get("gross_profit"),
        operating_income  = pnl.get("operating_income"),
        net_income        = pnl.get("net_income"),
        cogs              = pnl.get("cogs"),
        rd_expense        = pnl.get("rd_expense"),
        sga_expense       = pnl.get("sga_expense"),
        interest_expense  = pnl.get("interest_expense"),
        income_tax        = pnl.get("income_tax"),
        currency          = pnl.get("currency", "USD") or "USD",
        extraction_method = method,
    )


def _rescale_segments_if_needed(sd: SegmentData) -> None:
    """Rescale segment values to match total_revenue if off by more than 25%."""
    if not sd.total_revenue or abs(sd.total_revenue) < 1e6 or not sd.segments:
        return
    seg_sum = sum(s.value for s in sd.segments if s.value)
    if seg_sum <= 0:
        return
    scale = sd.total_revenue / seg_sum
    if SEGMENT_RESCALE_MIN <= scale <= SEGMENT_RESCALE_MAX:
        return
    for s in sd.segments:
        if s.value:
            s.value *= scale


def _pnl_from_yahoo(
    yahoo_data: Dict, period: str, is_annual: bool, usd_rate: float,
) -> Dict:
    key    = "annual_income" if is_annual else "quarterly_income"
    src    = yahoo_data.get(key, {}) or {}
    if not src:
        return {"currency": yahoo_data.get("currency", "USD")}
    latest = src[sorted(src.keys(), reverse=True)[0]]

    def _m(k):
        v = latest.get(k)
        return v * usd_rate if v is not None else None

    total_revenue = _m("Total Revenue")

    # ── Insurance-specific Yahoo Finance line items ───────────────────────────
    # Insurance companies (Allianz, AIG, Zurich) expose policyholder benefits
    # as COGS.  Map them to cogs/gross_profit before bank logic overrides.
    insurance_claims = (
        _m("Net Policyholder Benefits And Claims") or
        _m("Policyholder Benefits And Claims") or
        _m("Total Policy Holder Benefits") or
        _m("Policy Holder Benefits")
    )
    ins_cogs_slot = None
    ins_gp_slot   = None
    if insurance_claims and total_revenue and 0 < insurance_claims < total_revenue * INSURANCE_CLAIMS_MAX_PCT:
        ins_cogs_slot = insurance_claims
        ins_gp_slot   = total_revenue - insurance_claims

    # ── Bank-specific Yahoo Finance line items ────────────────────────────────
    # yfinance exposes dedicated bank income statement fields that map directly
    # to the bank P&L structure when present.
    bank_provision = (
        _m("Provision For Loan Losses") or
        _m("Provision For Credit Losses") or
        _m("Provision For Doubtful Accounts") or
        _m("Credit Loss Provision")
    )
    bank_nii    = _m("Net Interest Income")
    # Only treat as a bank (NII-driven) when NII is a significant positive value
    # (> 5% of revenue).  Insurance companies often have small/negative "Net
    # Interest Income" from investment portfolios — don't confuse them with banks.
    _nii_is_bank = (
        bank_nii is not None and bank_nii > 0
        and total_revenue and bank_nii > total_revenue * BANK_NII_MIN_PCT
    )
    bank_nonint = _m("Non Interest Income") or _m("Net Non Interest Income")
    bank_nie = (
        _m("Non Interest Expense") or
        _m("Total Non Interest Expense") or
        _m("Non-Interest Expense") or
        # HSBC and some European banks report total NIE as "Operating Expense"
        (_m("Operating Expense") if _nii_is_bank else None)
    )

    # Derive Non-Interest Income when NII is known but the split isn't explicit
    if _nii_is_bank and bank_nonint is None and total_revenue is not None:
        implied_nonint = total_revenue - bank_nii
        if 0 < implied_nonint < total_revenue:
            bank_nonint = implied_nonint

    # interest_expense slot: prefer bank provision when it's reasonable (< 15%),
    # otherwise discard the gross interest expense which banks report separately.
    raw_ie = _m("Interest Expense")
    if bank_provision is not None and total_revenue and 0 < bank_provision < total_revenue * BANK_PROVISION_MAX_PCT:
        ie_slot = bank_provision
    elif raw_ie is not None and total_revenue and raw_ie < total_revenue * BANK_PROVISION_MAX_PCT:
        ie_slot = raw_ie
    else:
        ie_slot = None   # gross interest expense >> provision; don't misuse as provision

    # sga_expense slot: prefer bank NIE field over generic SG&A
    sga_raw = _m("Selling General And Administration")
    if bank_nie is not None and total_revenue and 0 < bank_nie < total_revenue:
        sga_slot = bank_nie
    else:
        sga_slot = sga_raw

    # operating_income: banks expose "Pretax Income" rather than "Operating Income"
    raw_op      = _m("Operating Income")
    bank_pretax = _m("Pretax Income") if _nii_is_bank else None
    # Fallback: use Pretax Income or EBIT when Operating Income is not available
    op_slot = raw_op or bank_pretax or _m("Pretax Income") or _m("EBIT")

    return {
        "total_revenue":    total_revenue,
        # Insurance cogs/gp override standard "Cost Of Revenue" / "Gross Profit"
        "gross_profit":     ins_gp_slot   or _m("Gross Profit"),
        "operating_income": op_slot,
        "net_income":       _m("Net Income"),
        "cogs":             ins_cogs_slot or _m("Cost Of Revenue"),
        "rd_expense":       _m("Research And Development"),
        "sga_expense":      sga_slot,
        "interest_expense": ie_slot,
        "income_tax":       _m("Tax Provision"),
        "currency":         "USD",
        # Bank extras — stored in notes by extract_for_yahoo_only for Sankey
        "bank_nii":         bank_nii if _nii_is_bank else None,
        "bank_nonint":      bank_nonint,
    }