"""Routes each ticker to the correct data sources based on its classification."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import FILING_TYPES_US, FILING_TYPES_INTL, YEARS_BACK
from src.ingestion.ticker_loader import TickerInfo
from src.ingestion.edgar_client import get_filings as _edgar_get_filings
from src.ingestion.yahoo_client import get_financials, get_total_revenue_by_period
from src.extraction.models import FilingRecord


def get_filings_for_ticker(info: TickerInfo) -> List[FilingRecord]:
    """Return FilingRecord list based on ticker classification."""
    if info.classification == "US_SEC":
        return _edgar_get_filings(info.ticker, FILING_TYPES_US, YEARS_BACK, cik=info.cik)

    if info.classification == "INTL_SEC":
        filings = _edgar_get_filings(info.ticker, FILING_TYPES_INTL, YEARS_BACK, cik=info.cik)
        if not filings:
            # Some INTL_SEC tickers also file 10-K / 10-Q under their ADR ticker
            filings = _edgar_get_filings(info.ticker, ["20-F", "40-F", "6-K", "10-K"], YEARS_BACK, cik=info.cik)
        return filings

    # INTL_YAHOO — no SEC filings
    return []


def get_yahoo_data(info: TickerInfo) -> Dict:
    """Always fetch Yahoo Finance data (supplement or primary)."""
    return get_financials(info.ticker)


def get_revenue_validation(info: TickerInfo) -> Dict[str, Optional[float]]:
    """{period: total_revenue_usd} from Yahoo Finance for cross-validation."""
    return get_total_revenue_by_period(info.ticker)


def summarize_coverage(info: TickerInfo, filings: List[FilingRecord]) -> Dict:
    annuals    = [f for f in filings if f.is_annual]
    quarterlys = [f for f in filings if not f.is_annual]
    return {
        "ticker":             info.ticker,
        "classification":     info.classification,
        "annual_count":       len(annuals),
        "quarterly_count":    len(quarterlys),
        "annual_periods":     sorted({f.period for f in annuals}),
        "quarterly_periods":  sorted({f.period for f in quarterlys}),
        "years_covered":      sorted({f.fiscal_year for f in filings}),
        "data_complete":      len(annuals) >= 3 and len(quarterlys) >= 8,
    }
