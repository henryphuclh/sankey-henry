"""Shared data models for financial segment extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SegmentValue:
    segment_name: str
    value:        float   # absolute USD
    unit:         str     # "USD", "EUR", …
    period:       str     # "FY2024", "2024Q3"
    concept:      str     # XBRL concept or "llm_extracted"
    is_annual:    bool


@dataclass
class SegmentData:
    ticker:           str
    period:           str
    is_annual:        bool
    fiscal_year:      int
    fiscal_quarter:   Optional[int]
    segments:         List[SegmentValue] = field(default_factory=list)
    total_revenue:    Optional[float] = None
    gross_profit:     Optional[float] = None
    operating_income: Optional[float] = None
    net_income:       Optional[float] = None
    rd_expense:       Optional[float] = None
    sga_expense:      Optional[float] = None
    cogs:             Optional[float] = None
    interest_expense: Optional[float] = None
    income_tax:       Optional[float] = None
    currency:         str = "USD"
    extraction_method: str = "edgar"
    confidence:       float = 0.0
    notes:            List[str] = field(default_factory=list)
    # Optional 2-layer segment data: top-level segments in `segments`,
    # product-level breakdown in `sub_segments`, mapping in `segment_hierarchy`.
    sub_segments:      Optional[List[SegmentValue]] = None
    segment_hierarchy: Optional[Dict[str, List[str]]] = None  # {top_seg: [product_names]}

    def to_dict(self) -> Dict:
        return {
            "ticker":           self.ticker,
            "period":           self.period,
            "is_annual":        self.is_annual,
            "fiscal_year":      self.fiscal_year,
            "fiscal_quarter":   self.fiscal_quarter,
            "segments":         [vars(s) for s in self.segments],
            "total_revenue":    self.total_revenue,
            "gross_profit":     self.gross_profit,
            "operating_income": self.operating_income,
            "net_income":       self.net_income,
            "rd_expense":       self.rd_expense,
            "sga_expense":      self.sga_expense,
            "cogs":             self.cogs,
            "interest_expense": self.interest_expense,
            "income_tax":       self.income_tax,
            "currency":         self.currency,
            "extraction_method":self.extraction_method,
            "confidence":       self.confidence,
            "notes":            self.notes,
            "sub_segments":     [vars(s) for s in self.sub_segments] if self.sub_segments else None,
            "segment_hierarchy":self.segment_hierarchy,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "SegmentData":
        d = dict(d)
        segs     = [SegmentValue(**s) for s in d.pop("segments", [])]
        raw_sub  = d.pop("sub_segments", None)
        sub_segs = [SegmentValue(**s) for s in raw_sub] if raw_sub else None
        hier     = d.pop("segment_hierarchy", None)
        known    = {f.name for f in cls.__dataclass_fields__.values()}
        obj      = cls(**{k: v for k, v in d.items() if k in known})
        obj.segments         = segs
        obj.sub_segments     = sub_segs
        obj.segment_hierarchy = hier
        return obj


@dataclass
class FilingRecord:
    ticker:           str
    form_type:        str   # "10-K", "10-Q", "20-F", "6-K"
    period:           str   # "FY2024", "2024Q3"
    filing_date:      str   # ISO date
    accession_number: str
    cik:              str
    is_annual:        bool
    fiscal_year:      int
    fiscal_quarter:   Optional[int]


def compute_confidence(sd: SegmentData) -> float:
    """0–1 score of data completeness."""
    score = 0.0
    if sd.total_revenue:
        score += 0.4
    if sd.gross_profit is not None:     score += 0.1
    if sd.operating_income is not None: score += 0.15
    if sd.net_income is not None:       score += 0.15
    pnl_count = sum(1 for f in (sd.rd_expense, sd.sga_expense, sd.cogs,
                                sd.interest_expense, sd.income_tax) if f is not None)
    score += min(pnl_count * 0.04, 0.20)
    if len(sd.segments) == 0:
        score = min(score, 0.55)
    elif len(sd.segments) >= 2:
        score = min(1.0, score + 0.30)
    else:
        score = min(1.0, score + 0.10)
    return round(score, 3)
