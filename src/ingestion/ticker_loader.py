from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import EXCEL_PATH, TICKER_TO_CIK
from src.ingestion.data_source_checker import check_ticker, CheckResult


@dataclass
class TickerInfo:
    ticker:         str
    name:           str
    market_cap_b:   Optional[float]
    classification: str       # "US_SEC" | "INTL_SEC" | "INTL_YAHOO"
    sector:         str       # "financial" | "pharma" | "conglomerate" | "standard"
    cik:            Optional[str] = None
    exchange:       str = ""


def _exchange_from_ticker(ticker: str) -> str:
    suffix_map = {
        ".KS": "KRX",       ".HK": "HKEX",   ".PA": "Euronext Paris",
        ".SW": "SIX",       ".DE": "XETRA",  ".L": "LSE",
        ".TO": "TSX",       ".AX": "ASX",    ".MC": "BME",
        ".CO": "Nasdaq Copenhagen",           ".T": "TSE",
    }
    for suffix, exchange in suffix_map.items():
        if ticker.endswith(suffix):
            return exchange
    return "NASDAQ/NYSE"


def load_tickers(path: Path = EXCEL_PATH) -> Dict[str, TickerInfo]:
    """Load all tickers from Excel and classify them automatically via data_source_checker."""
    df = pd.read_excel(path, sheet_name="Valuation Data")
    df.columns = [c.strip() for c in df.columns]

    tickers: Dict[str, TickerInfo] = {}
    for _, row in df.iterrows():
        ticker = str(row.get("Ticker", "")).strip()
        if not ticker:
            continue

        name   = str(row.get("Company", "")).strip()
        mc_raw = row.get("MarketCap (B $)", None)
        try:
            mc = float(mc_raw) if mc_raw not in (None, "N/A", "") else None
        except (ValueError, TypeError):
            mc = None

        result: CheckResult = check_ticker(ticker)

        tickers[ticker] = TickerInfo(
            ticker         = ticker,
            name           = name,
            market_cap_b   = mc,
            classification = result.classification,
            sector         = result.sector,
            cik            = result.cik or TICKER_TO_CIK.get(ticker),
            exchange       = _exchange_from_ticker(ticker),
        )

    return tickers


def get_ticker_list() -> List[TickerInfo]:
    return list(load_tickers().values())


def print_classification_table() -> None:
    tickers = load_tickers()
    us   = [t for t in tickers.values() if t.classification == "US_SEC"]
    isec = [t for t in tickers.values() if t.classification == "INTL_SEC"]
    iyf  = [t for t in tickers.values() if t.classification == "INTL_YAHOO"]

    print(f"\n{'='*60}")
    print(f"  Ticker Universe: {len(tickers)} stocks")
    print(f"{'='*60}")
    print(f"  US_SEC    (10-K/10-Q on EDGAR): {len(us):>3} tickers")
    print(f"  INTL_SEC  (20-F/6-K on EDGAR):  {len(isec):>3} tickers  — {[t.ticker for t in isec]}")
    print(f"  INTL_YAHOO (Yahoo Finance only): {len(iyf):>3} tickers  — {[t.ticker for t in iyf]}")
    print(f"\n  Sectors:")
    for sector in ("financial", "pharma", "conglomerate", "standard"):
        subset = [t.ticker for t in tickers.values() if t.sector == sector]
        print(f"    {sector:<14}: {len(subset):>3}  — {subset}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    print_classification_table()