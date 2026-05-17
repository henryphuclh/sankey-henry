"""Aggregate SegmentData across multiple periods into a single company summary."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DATA_DIR
from src.extraction.models import SegmentData

_SEGMENTS_DIR = DATA_DIR / "segments"
_SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class AggregatedCompanyData:
    ticker:       str
    name:         str
    sector:       str = ""   # sector label for report rendering and Sankey routing

    # Latest annual period (most recent FY)
    latest_annual:     Optional[SegmentData] = None
    # All annual periods for trend
    annual_periods:    List[SegmentData] = field(default_factory=list)
    # All quarterly periods (up to 12)
    quarterly_periods: List[SegmentData] = field(default_factory=list)

    # TTM (sum of last 4 quarters, or latest annual if < 4 quarters available)
    ttm_revenue:          Optional[float] = None
    ttm_operating_income: Optional[float] = None
    ttm_net_income:       Optional[float] = None

    # Segment name → [{period, value, is_annual}, ...] sorted by period
    segment_trend: Dict[str, List[Dict]] = field(default_factory=dict)

    # Data source classification (US_SEC | INTL_SEC | INTL_YAHOO)
    classification: str = ""

    # Coverage flags
    annual_count:    int  = 0
    quarterly_count: int  = 0
    has_partial_data: bool = False
    data_notes:      List[str] = field(default_factory=list)

    def get_sankey_data_for_period(self, period: str) -> Optional[SegmentData]:
        for sd in self.annual_periods:
            if sd.period == period:
                return sd
        for sd in self.quarterly_periods:
            if sd.period == period:
                return sd
        return self.latest_annual

    def available_annual_periods(self) -> List[str]:
        return sorted({sd.period for sd in self.annual_periods}, reverse=True)

    def to_dict(self) -> Dict:
        return {
            "ticker":           self.ticker,
            "name":             self.name,
            "sector":           self.sector,
            "classification":   self.classification,
            "latest_annual":    self.latest_annual.to_dict() if self.latest_annual else None,
            "annual_periods":   [sd.to_dict() for sd in self.annual_periods],
            "quarterly_periods": [sd.to_dict() for sd in self.quarterly_periods],
            "ttm_revenue":       self.ttm_revenue,
            "ttm_operating_income": self.ttm_operating_income,
            "ttm_net_income":    self.ttm_net_income,
            "segment_trend":     self.segment_trend,
            "annual_count":      self.annual_count,
            "quarterly_count":   self.quarterly_count,
            "has_partial_data":  self.has_partial_data,
            "data_notes":        self.data_notes,
        }


def aggregate(
    ticker:      str,
    name:        str,
    all_periods: List[SegmentData],
    sector:      str = "",
) -> AggregatedCompanyData:
    """
    Combine SegmentData list (annual + quarterly) into AggregatedCompanyData.
    Deduplicates amended filings (10-K/A, 10-Q/A): keeps the entry with the
    highest confidence score per period label.
    Saves result to data/segments/{ticker}.json.
    """
    all_periods = _dedup(all_periods)

    annuals    = sorted([sd for sd in all_periods if sd.is_annual],
                        key=lambda s: s.period, reverse=True)
    quarterlys = sorted([sd for sd in all_periods if not sd.is_annual],
                        key=lambda s: s.period, reverse=True)

    agg = AggregatedCompanyData(
        ticker            = ticker,
        name              = name,
        sector            = sector,
        latest_annual     = annuals[0] if annuals else None,
        annual_periods    = annuals,
        quarterly_periods = quarterlys,
        annual_count      = len(annuals),
        quarterly_count   = len(quarterlys),
        has_partial_data  = len(annuals) < 3 or len(quarterlys) < 12,
    )

    if agg.has_partial_data:
        agg.data_notes.append(
            f"Only {len(annuals)}/3 annual report{'s' if len(annuals) != 1 else ''} available"
        )

    # TTM from last 4 quarters
    last4q = quarterlys[:4]
    if len(last4q) >= 4:
        agg.ttm_revenue          = _sum_field(last4q, "total_revenue")
        agg.ttm_operating_income = _sum_field(last4q, "operating_income")
        agg.ttm_net_income       = _sum_field(last4q, "net_income")
    elif annuals:
        agg.ttm_revenue          = annuals[0].total_revenue
        agg.ttm_operating_income = annuals[0].operating_income
        agg.ttm_net_income       = annuals[0].net_income

    agg.segment_trend = _build_segment_trend(all_periods)
    _save(agg)
    return agg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup(periods: List[SegmentData]) -> List[SegmentData]:
    """
    Keep only one SegmentData per period label.
    When duplicates exist (amended filings), retain the one with the higher
    confidence score; tie-break by keeping the later extraction (last in list).
    """
    best: Dict[str, SegmentData] = {}
    for sd in periods:
        key = sd.period
        existing = best.get(key)
        if existing is None or sd.confidence >= existing.confidence:
            best[key] = sd
    return list(best.values())


def _sum_field(periods: List[SegmentData], fld: str) -> Optional[float]:
    vals = [getattr(sd, fld) for sd in periods if getattr(sd, fld) is not None]
    return sum(vals) if vals else None


def _build_segment_trend(periods: List[SegmentData]) -> Dict[str, List[Dict]]:
    trend: Dict[str, List[Dict]] = {}
    for sd in sorted(periods, key=lambda s: s.period):
        for seg in sd.segments:
            if seg.segment_name not in trend:
                trend[seg.segment_name] = []
            trend[seg.segment_name].append({
                "period":    sd.period,
                "value":     seg.value,
                "is_annual": sd.is_annual,
            })
    return trend


def _save(agg: AggregatedCompanyData) -> None:
    path = _SEGMENTS_DIR / f"{agg.ticker}.json"
    try:
        path.write_text(
            json.dumps(agg.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_cached(ticker: str) -> Optional[AggregatedCompanyData]:
    path = _SEGMENTS_DIR / f"{ticker}.json"
    if not path.exists():
        return None
    try:
        d   = json.loads(path.read_text(encoding="utf-8"))
        agg = AggregatedCompanyData(
            ticker            = d["ticker"],
            name              = d["name"],
            sector            = d.get("sector", ""),
            classification    = d.get("classification", ""),
            ttm_revenue       = d.get("ttm_revenue"),
            ttm_operating_income = d.get("ttm_operating_income"),
            ttm_net_income    = d.get("ttm_net_income"),
            segment_trend     = d.get("segment_trend", {}),
            annual_count      = d.get("annual_count", 0),
            quarterly_count   = d.get("quarterly_count", 0),
            has_partial_data  = d.get("annual_count", 0) < 3 or d.get("quarterly_count", 0) < 12,
            data_notes        = d.get("data_notes", []),
        )
        if d.get("latest_annual"):
            agg.latest_annual = SegmentData.from_dict(d["latest_annual"])
        agg.annual_periods    = [SegmentData.from_dict(x) for x in d.get("annual_periods", [])]
        agg.quarterly_periods = [SegmentData.from_dict(x) for x in d.get("quarterly_periods", [])]
        return agg
    except Exception:
        return None