"""Tests for ticker loader."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingestion.ticker_loader import load_tickers, TickerInfo


def test_load_all_98_tickers():
    tickers = load_tickers()
    assert len(tickers) == 98, f"Expected 98 tickers, got {len(tickers)}"


def test_known_us_sec():
    tickers = load_tickers()
    assert tickers["MSFT"].classification == "US_SEC"
    assert tickers["NVDA"].classification == "US_SEC"
    assert tickers["AAPL"].classification == "US_SEC"


def test_known_intl_sec():
    tickers = load_tickers()
    assert tickers["TSM"].classification == "INTL_SEC"
    assert tickers["ASML"].classification == "INTL_SEC"


def test_known_intl_yahoo():
    tickers = load_tickers()
    assert tickers["005930.KS"].classification == "INTL_YAHOO"
    assert tickers["MC.PA"].classification == "INTL_YAHOO"
    assert tickers["NESN.SW"].classification == "INTL_YAHOO"


def test_sector_classification():
    tickers = load_tickers()
    assert tickers["JPM"].sector == "financial"
    assert tickers["LLY"].sector == "pharma"
    assert tickers["BRK-B"].sector == "conglomerate"
    assert tickers["NVDA"].sector == "standard"


def test_ticker_info_fields():
    tickers = load_tickers()
    msft = tickers["MSFT"]
    assert isinstance(msft, TickerInfo)
    assert msft.ticker == "MSFT"
    assert msft.name  # should have a name
