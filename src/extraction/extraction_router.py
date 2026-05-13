"""Orchestrate per-filing extraction: edgartools for P&L + LLM for segments.

Flow:
1. Parse filing with edgartools (TenK / TenQ / TwentyF).
2a. Financial sector  → pnl_from_financial_filing() (bank/insurance P&L logic).
2b. All others        → pnl_from_filing_obj() (standard income statement).
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
from src.extraction.models import SegmentData, SegmentValue, FilingRecord, compute_confidence
from src.extraction.llm_extractor import (
    extract_segments, extract_segments_from_yahoo_summary,
)
from src.ingestion.ticker_loader import TickerInfo
from src.ingestion.edgar_client import (
    get_filing_obj, get_segment_note_text, pnl_from_filing_obj,
    segments_from_xbrl_dimensions,
)


# Geographic keywords used to detect geo-only XBRL segments.
# Expanded to cover BAC ("U.S.", "Non-U.S.") and other common patterns.
_GEO_KEYWORDS = {
    # Continental / regional
    "americas", "north america", "latin america", "south america",
    "europe", "emea", "middle east", "africa",
    "asia", "asia pacific", "asia-pacific", "apac",
    "greater china", "china", "japan", "rest of asia",
    # Domestic / international splits (BAC, WFC, C style)
    "u.s.", "non-u.s.", "non-us", "domestic", "international",
    "united states", "outside u.s.", "outside the u.s.",
    # Other common geo labels
    "other regions", "rest of world", "worldwide",
}


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

    # 2) P&L: financial sector uses bank/insurance handler; all others use standard.
    # Accept any case variant: "Financials", "Financial Services", "financial", etc.
    _sector_lower = (sector or "").lower()
    _is_financial = "financial" in _sector_lower or "banking" in _sector_lower or "insurance" in _sector_lower
    if _is_financial:
        from src.extraction.sector_handlers.financials import pnl_from_financial_filing
        pnl = pnl_from_financial_filing(obj)
    else:
        pnl = pnl_from_filing_obj(obj)

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

    # 3) Segment extraction: XBRL-first unless geo-only
    segments: List[SegmentValue] = segments_from_xbrl_dimensions(obj, total_revenue=total_rev)
    method = "xbrl" if segments else "edgar"

    # Drop sub-line segments with negligible values (< 0.5% of total revenue or < $50M)
    # This removes artifacts like "Interchange and merchant services fees - WIM: $0B" in WFC
    if segments and total_rev:
        segments = [s for s in segments if s.value and s.value >= max(total_rev * 0.005, 50e6)]
    elif segments:
        segments = [s for s in segments if s.value and s.value >= 50e6]

    # Detect geo-only XBRL (e.g. AAPL geographic axis, BAC U.S./Non-U.S.)
    geo_only = _is_geo_only(segments)

    xbrl_coverage = 0.0
    if segments and total_rev and not geo_only:
        xbrl_coverage = sum(s.value for s in segments if s.value) / total_rev

    use_llm = geo_only or (not segments) or (xbrl_coverage < 0.70)

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
                llm_sum = sum(s.value for s in llm_segs)
                llm_cov = llm_sum / total_rev if total_rev else 0
                if geo_only or not segments or llm_cov > xbrl_coverage:
                    segments = llm_segs
                    method   = "edgar+llm"
                    xbrl_coverage = llm_cov
        else:
            # Step 5: mandatory fallback via raw filing text
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

    # Stash bank expense sub-components in notes for sankey_builder
    expense_detail = pnl.get("expense_detail")
    if expense_detail:
        sd.notes.append(f"EXPENSE_DETAIL:{json.dumps(expense_detail)}")

    # Yahoo revenue sanity check
    if known_rev and sd.total_revenue:
        diff = abs(sd.total_revenue - known_rev) / max(abs(known_rev), 1)
        if diff > 0.15:
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
    segments = extract_segments_from_yahoo_summary(ticker, period, yahoo_data, sector)

    sd = _build_segment_data(
        ticker=ticker, period=period, is_annual=is_annual,
        fy=fy, fq=None,
        pnl=pnl, segments=segments,
        method="yahoo+llm",
    )
    sd.notes.append("Source: Yahoo Finance (no SEC filing available)")
    sd.notes.append("⚠ Limited Segment Detail — aggregate data only")
    sd.confidence = compute_confidence(sd)
    return sd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import json  # noqa: E402 (needed here for expense_detail serialisation)


def _is_geo_only(segments: List[SegmentValue]) -> bool:
    """Return True if every segment name is a geographic keyword."""
    if not segments:
        return False
    return all(
        any(kw in s.segment_name.lower() for kw in _GEO_KEYWORDS)
        for s in segments
    )


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
    if 0.80 <= scale <= 1.25:
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

    return {
        "total_revenue":    _m("Total Revenue"),
        "gross_profit":     _m("Gross Profit"),
        "operating_income": _m("Operating Income"),
        "net_income":       _m("Net Income"),
        "cogs":             _m("Cost Of Revenue"),
        "rd_expense":       _m("Research And Development"),
        "sga_expense":      _m("Selling General And Administration"),
        "interest_expense": _m("Interest Expense"),
        "income_tax":       _m("Tax Provision"),
        "currency":         "USD",
    }