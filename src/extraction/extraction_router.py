"""Orchestrate per-filing extraction: edgartools for P&L + LLM for segments.

Flow:
1. Parse filing with edgartools (TenK / TenQ / TwentyF).
2a. Financial sector  → sector_handlers.financials.pnl_from_financial_filing()
2b. Pharma sector     → sector_handlers.standard.pnl_from_standard_filing() + has_pharma_indicators() check
2c. Standard sector   → sector_handlers.standard.pnl_from_standard_filing()
3. Detect geo-only XBRL → bypass XBRL, use LLM from Revenue/MD&A note.
4. Find Segment note; feed to LLM if XBRL coverage < 70%.
5. Mandatory LLM fallback from raw filing text when note_text is empty.
6. Rescale LLM segments if they are wildly off vs edgartools total.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import (
    XBRL_COVERAGE_MIN, SEGMENT_MIN_PCT, SEGMENT_MIN_VALUE,
    SEGMENT_RESCALE_MIN, SEGMENT_RESCALE_MAX, REVENUE_WARN_DIFF,
)
from src.extraction.models import SegmentData, SegmentValue, FilingRecord, compute_confidence
from src.extraction.llm_extractor import extract_segments
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
    yahoo_data:  Optional[Dict] = None,
    **_ignored,
) -> SegmentData:
    """Extract SegmentData for one filing using edgartools + LLM."""
    ticker      = ticker_info.ticker
    period      = filing.period
    sector      = ticker_info.sector
    data_source = ticker_info.classification  # "US_SEC" | "INTL_SEC" | "INTL_YAHOO"

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
        pnl = pnl_from_financial_filing(obj, data_source=data_source)
        _check_rev = pnl.get("total_revenue")
        if _check_rev and not has_bank_indicators(pnl, _check_rev):
            from src.extraction.sector_handlers.standard import pnl_from_standard_filing
            pnl = pnl_from_standard_filing(obj, data_source=data_source)
            _effective_sector = "standard"
            _is_financial = False
    elif _sector_lower == "pharma":
        from src.extraction.sector_handlers.standard import pnl_from_standard_filing
        from src.extraction.sector_handlers.pharma import has_pharma_indicators, is_too_granular, strip_product_level_from_llm
        pnl = pnl_from_standard_filing(obj, data_source=data_source)
        _check_rev = pnl.get("total_revenue")
        if _check_rev and not has_pharma_indicators(pnl, _check_rev):
            _effective_sector = "standard"
    else:
        from src.extraction.sector_handlers.standard import pnl_from_standard_filing
        pnl = pnl_from_standard_filing(obj, data_source=data_source)

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

    _pharma_too_granular = _sector_lower == "pharma" and not geo_only and is_too_granular(segments)

    use_llm = geo_only or (not segments) or (xbrl_coverage < XBRL_COVERAGE_MIN) or _pharma_too_granular

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
                if _sector_lower == "pharma":
                    llm_segs = strip_product_level_from_llm(llm_segs)
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

    # Currency scaling: INTL_SEC companies that file in a non-USD currency
    # (e.g. TSM=TWD, TM/6758.T/8306.T=JPY, NOVO-B.CO=DKK, 9988.HK=CNY/HKD).
    # Scale factor = yahoo_revenue_usd / xbrl_revenue_native, which implicitly
    # captures the period-average FX rate without a separate API call.
    if data_source == "INTL_SEC" and total_rev and known_rev:
        _fx_scale = _detect_currency_scale(total_rev, known_rev)
        if _fx_scale is not None:
            pnl      = _scale_pnl_to_usd(pnl, _fx_scale)
            for s in segments:
                if s.value:
                    s.value *= _fx_scale
            total_rev = pnl.get("total_revenue")

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
    ticker_info:    TickerInfo,
    period:         str,
    yahoo_data:     Dict,
    is_annual:      bool = True,
    fiscal_year:    Optional[int] = None,
    yahoo_date_key: Optional[str] = None,
) -> SegmentData:
    """INTL_YAHOO path: no SEC filing — use Yahoo data + LLM for segments."""
    ticker   = ticker_info.ticker
    sector   = ticker_info.sector
    fy       = fiscal_year or (int(period[2:6]) if period.startswith("FY") else int(period[:4]))
    usd_rate = yahoo_data.get("usd_rate", 1.0)

    pnl      = _pnl_from_yahoo(yahoo_data, period, is_annual, usd_rate, date_key=yahoo_date_key)
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


def _detect_currency_scale(xbrl_rev: float, yahoo_rev_usd: float) -> Optional[float]:
    """Return USD scale factor when XBRL values are in a non-USD currency.

    Triggered when xbrl/yahoo ratio > 2 (JPY ~150x, TWD ~32x, DKK ~7x, CNY ~7x).
    Scale = yahoo_rev_usd / xbrl_rev_native, which equals 1/fx_rate implicitly.
    """
    if xbrl_rev <= 0 or yahoo_rev_usd <= 0:
        return None
    if xbrl_rev / yahoo_rev_usd < 2.0:
        return None
    return yahoo_rev_usd / xbrl_rev


def _scale_pnl_to_usd(pnl: Dict, scale: float) -> Dict:
    """Multiply all monetary P&L fields by scale (native currency → USD)."""
    _MONETARY = frozenset({
        "total_revenue", "gross_profit", "operating_income", "net_income",
        "cogs", "rd_expense", "sga_expense", "interest_expense", "income_tax",
    })
    return {
        k: (v * scale if k in _MONETARY and isinstance(v, (int, float)) else v)
        for k, v in pnl.items()
    }


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
    date_key: Optional[str] = None,
) -> Dict:
    from src.extraction.sector_handlers.financials import pnl_fields_for_yahoo_financial

    key = "annual_income" if is_annual else "quarterly_income"
    src = yahoo_data.get(key, {}) or {}
    if not src:
        return {"currency": yahoo_data.get("currency", "USD")}
    row = src[date_key] if (date_key and date_key in src) else src[sorted(src.keys(), reverse=True)[0]]

    def _m(k):
        v = row.get(k)
        return v * usd_rate if v is not None else None

    total_revenue = _m("Total Revenue")
    fin = pnl_fields_for_yahoo_financial(_m, total_revenue)

    return {
        "total_revenue":    total_revenue,
        "gross_profit":     fin["gross_profit"],
        "operating_income": fin["operating_income"],
        "net_income":       _m("Net Income"),
        "cogs":             fin["cogs"],
        "rd_expense":       _m("Research And Development"),
        "sga_expense":      fin["sga_expense"],
        "interest_expense": fin["interest_expense"],
        "income_tax":       _m("Tax Provision"),
        "currency":         "USD",
        "bank_nii":         fin["bank_nii"],
        "bank_nonint":      fin["bank_nonint"],
    }


# ---------------------------------------------------------------------------
# Quarterly gap-filling (US_SEC only)
# ---------------------------------------------------------------------------

def fill_quarterly_gaps_from_yahoo(
    all_sds:     "List[SegmentData]",
    yahoo_data:  Dict,
    ticker_info: "TickerInfo",
) -> "List[SegmentData]":
    """Fill missing quarterly P&L periods for US_SEC companies from Yahoo Finance.

    edgartools labels 10-Q periods by the calendar quarter of the period-end
    date ({year}Q{cal_quarter}).  For companies whose fiscal year does not end
    in December, the standalone fiscal-year-end quarter has no 10-Q filing
    (it lives inside the 10-K).  Yahoo Finance provides all four calendar
    quarters as standalone rows, so we use it to fill the gap.

    Logic: for every Yahoo quarterly date not already covered by a period
    label in all_sds, create a P&L-only SegmentData (segments=[]) using the
    same {year}Q{cal_quarter} convention as edgartools.
    """
    import pandas as pd
    from config import QUARTERS_BACK

    qtr_income = (yahoo_data or {}).get("quarterly_income") or {}
    if not qtr_income:
        return []

    usd_rate = yahoo_data.get("usd_rate", 1.0)
    existing_periods = {sd.period for sd in all_sds}

    gap_fills: List[SegmentData] = []

    for date_str in sorted(qtr_income.keys(), reverse=True)[:QUARTERS_BACK]:
        try:
            d    = pd.Timestamp(date_str)
            cal_q = (d.month - 1) // 3 + 1
            period = f"{d.year}Q{cal_q}"
        except Exception:
            continue

        if period in existing_periods:
            continue

        pnl = _pnl_from_yahoo(
            yahoo_data, period, is_annual=False,
            usd_rate=usd_rate, date_key=date_str,
        )
        if not pnl.get("total_revenue"):
            continue

        sd = _build_segment_data(
            ticker    = ticker_info.ticker,
            period    = period,
            is_annual = False,
            fy        = d.year,
            fq        = cal_q,
            pnl       = pnl,
            segments  = [],
            method    = "yahoo_quarterly",
        )
        sd.confidence = compute_confidence(sd)
        gap_fills.append(sd)

    return gap_fills


