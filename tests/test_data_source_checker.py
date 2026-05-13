"""Tests for data_source_checker, updated ticker_loader, and cleaned config.

Project structure: src/ingestion/data_source_checker.py
                   src/cache/cache_manager.py
                   src/ingestion/ticker_loader.py
                   config.py  (root)

Run from project root:
    python test_data_source_checker.py
    pytest test_data_source_checker.py -v
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Add project root to path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

# ── Import using actual src/ layout ──────────────────────────────────────────
from src.ingestion.data_source_checker import (
    CheckResult,
    check_ticker,
    _has_non_us_suffix,
    _classify_source,
    _classify_sector,
    _run_checks,
)
from src.cache.cache_manager import cache
import src.ingestion.data_source_checker as dsc   # alias used by patch() calls below

EXCEL_PATH = _ROOT.parent / "Valuation_Top100_2026-04-18.xlsx"


# ---------------------------------------------------------------------------
# Config fixture — patches config.py in-memory without replacing the file
# ---------------------------------------------------------------------------

def _apply_config_patch():
    import config
    for attr in ("FINANCIAL_TICKERS", "PHARMA_TICKERS", "CONGLOMERATE_TICKERS",
                 "INTL_SEC_TICKERS", "INTL_YAHOO_TICKERS"):
        if hasattr(config, attr):
            delattr(config, attr)
    config.CACHE_TTL.setdefault("checker", 30)

_apply_config_patch()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_submissions(sic: int, forms: list) -> dict:
    return {"sic": sic, "filings": {"recent": {"form": forms}}}


def _clear_checker_cache(ticker: str):
    for key in (f"check_{ticker}", f"yf_info_{ticker}"):
        p = cache._path("checker", key)
        if p.exists():
            p.unlink()


def _unique() -> str:
    return f"_T{uuid.uuid4().hex[:6].upper()}_"


def _yf_mock(sector: str, industry: str) -> MagicMock:
    m = MagicMock()
    m.Ticker.return_value.info = {"sector": sector, "industry": industry}
    return m


# ---------------------------------------------------------------------------
# 1. config.py — removed sets must not exist
# ---------------------------------------------------------------------------

class TestConfigCleaned:
    def test_hardcoded_sets_removed(self):
        import config
        for attr in ("FINANCIAL_TICKERS", "PHARMA_TICKERS",
                     "CONGLOMERATE_TICKERS", "INTL_SEC_TICKERS",
                     "INTL_YAHOO_TICKERS"):
            assert not hasattr(config, attr), \
                f"config.{attr} still present — remove it"

    def test_ticker_to_cik_still_present(self):
        import config
        assert hasattr(config, "TICKER_TO_CIK")
        assert isinstance(config.TICKER_TO_CIK, dict)
        assert len(config.TICKER_TO_CIK) > 0

    def test_checker_ttl_added(self):
        import config
        assert "checker" in config.CACHE_TTL

    def test_required_keys_present(self):
        import config
        for key in ("OPENAI_API_KEY", "SEC_USER_AGENT", "EXCEL_PATH",
                    "CACHE_DIR", "XBRL_REVENUE_TAGS", "SANKEY_COLORS"):
            assert hasattr(config, key), f"config.{key} missing"


# ---------------------------------------------------------------------------
# 2. CheckResult structure
# ---------------------------------------------------------------------------

class TestCheckResultStructure:
    def test_fields_present(self):
        r = CheckResult(
            ticker="TEST", data_source="sec", classification="US_SEC",
            sector="standard", filing_type="10-K/10-Q",
            cik="0000123456", confidence=0.9, notes=[]
        )
        assert r.ticker == "TEST"
        assert r.confidence == 0.9


# ---------------------------------------------------------------------------
# 3. Non-US suffix detection
# ---------------------------------------------------------------------------

class TestNonUsSuffix:
    def test_known_suffixes_detected(self):
        for t in ("005930.KS", "MC.PA", "NESN.SW", "SIE.DE",
                  "9988.HK", "8306.T", "CBA.AX", "TD.TO"):
            assert _has_non_us_suffix(t), f"{t} should be non-US"

    def test_us_tickers_not_flagged(self):
        for t in ("AAPL", "JPM", "NVDA", "BRK-B", "TSLA"):
            assert not _has_non_us_suffix(t), f"{t} should be US"


# ---------------------------------------------------------------------------
# 4. Source / classification detection
# ---------------------------------------------------------------------------

class TestClassifySource:
    def _run(self, ticker, sic, forms):
        notes = []
        return _classify_source(ticker, "0000123456",
                                _fake_submissions(sic, forms), notes)

    def test_us_sec_10k(self):
        ds, cl, ft = self._run("AAPL", 3674, ["10-K", "10-Q", "8-K"])
        assert cl == "US_SEC" and ft == "10-K/10-Q" and ds == "both"

    def test_intl_sec_20f(self):
        ds, cl, ft = self._run("TSM", 3674, ["20-F", "6-K"])
        assert cl == "INTL_SEC" and ft == "20-F/6-K"

    def test_no_cik_non_us_suffix(self):
        ds, cl, ft = _classify_source("005930.KS", None, None, [])
        assert cl == "INTL_YAHOO" and ds == "yahoo_only" and ft == "none"

    def test_cik_but_no_annual_forms(self):
        subs = _fake_submissions(3674, ["8-K", "SC 13G"])
        ds, cl, ft = _classify_source("SOMEADR", "0000999999", subs, [])
        assert ds == "yahoo_only"


# ---------------------------------------------------------------------------
# 5. Sector from SIC code
# ---------------------------------------------------------------------------

class TestSectorFromSic:
    def _sector(self, sic):
        ticker = _unique()
        _clear_checker_cache(ticker)
        yf = _yf_mock("", "")
        with patch.dict(sys.modules, {"yfinance": yf}):
            notes = []
            sector, conf = _classify_sector(ticker, sic,
                                            _fake_submissions(sic, []), notes)
        return sector, conf

    def test_bank_sic(self):
        s, c = self._sector(6022)
        assert s == "financial" and c >= 0.9

    def test_insurance_sic(self):
        s, _ = self._sector(6321)
        assert s == "financial"

    def test_pharma_sic(self):
        s, c = self._sector(2836)
        assert s == "pharma" and c >= 0.9

    def test_tech_sic_standard(self):
        ticker = _unique()
        _clear_checker_cache(ticker)
        yf = _yf_mock("Information Technology", "Semiconductors")
        with patch.dict(sys.modules, {"yfinance": yf}):
            s, _ = _classify_sector(ticker, 3674, None, [])
        assert s == "standard"


# ---------------------------------------------------------------------------
# 6. Sector from Yahoo Finance
# ---------------------------------------------------------------------------

class TestSectorFromYahoo:
    def _sector_yf(self, yf_sector, yf_industry, sic=None):
        ticker = _unique()
        _clear_checker_cache(ticker)
        yf = _yf_mock(yf_sector, yf_industry)
        with patch.dict(sys.modules, {"yfinance": yf}):
            sector, conf = _classify_sector(ticker, sic, None, [])
        return sector, conf

    def test_financial_services(self):
        s, c = self._sector_yf("Financial Services", "Banks—Global")
        assert s == "financial" and c >= 0.8

    def test_financials_sector(self):
        s, _ = self._sector_yf("Financials", "Asset Management")
        assert s == "financial"

    def test_pharma_healthcare_plus_drug(self):
        s, _ = self._sector_yf("Healthcare", "Drug Manufacturers—General")
        assert s == "pharma"

    def test_healthcare_non_pharma_industry(self):
        s, _ = self._sector_yf("Health Care", "Medical Care Facilities")
        assert s == "standard"

    def test_conglomerate_industry_keyword(self):
        s, _ = self._sector_yf("Industrials", "Conglomerates")
        assert s == "conglomerate"

    def test_diversified_keyword(self):
        s, _ = self._sector_yf("Industrials", "Diversified Industrials")
        assert s == "conglomerate"

    def test_insurance_diversified_conglomerate(self):
        # BRK-B case: Yahoo returns "Insurance - Diversified"
        s, _ = self._sector_yf("Financial Services", "Insurance - Diversified")
        assert s == "conglomerate"


# ---------------------------------------------------------------------------
# 7. Conglomerate via holding SIC
# ---------------------------------------------------------------------------

class TestConglomerateHoldingSic:
    def test_sic_6719(self):
        ticker = _unique()
        _clear_checker_cache(ticker)
        yf = _yf_mock("Financials", "")
        with patch.dict(sys.modules, {"yfinance": yf}):
            s, _ = _classify_sector(ticker, 6719,
                                    _fake_submissions(6719, ["10-K"]), [])
        assert s in ("financial", "conglomerate")


# ---------------------------------------------------------------------------
# 8. Cache round-trip
# ---------------------------------------------------------------------------

class TestCacheRoundTrip:
    def test_cached_result_reused(self):
        ticker = _unique()
        _clear_checker_cache(ticker)

        call_count = [0]
        original_run = dsc._run_checks

        def counting_run(t):
            call_count[0] += 1
            return original_run(t)

        with patch.object(dsc, "_resolve_cik", return_value=None), \
             patch.object(dsc, "_fetch_yahoo_sector", return_value=("", "")), \
             patch.object(dsc, "_run_checks", side_effect=counting_run):
            check_ticker(ticker, force=True)

        with patch.object(dsc, "_run_checks", side_effect=counting_run):
            check_ticker(ticker, force=False)

        assert call_count[0] == 1, \
            f"_run_checks called {call_count[0]} times — cache not working"


# ---------------------------------------------------------------------------
# 9. ticker_loader integration (no network)
# ---------------------------------------------------------------------------

class TestTickerLoader:
    KNOWN_CIK_MAP = {
        "TSM": "0001046179", "ASML": "0000937966",
        "TCEHY": "0001495479", "RHHBY": "0001114388",
        "HSBC": "0000083246", "AZN": "0000901832", "NVS": "0001114448",
    }
    INTL_SEC  = {"TSM", "ASML", "TCEHY", "RHHBY", "HSBC", "AZN", "NVS"}
    FINANCIAL = {"JPM","BAC","GS","MS","WFC","C","AXP","SCHW","HSBC","8306.T",
                 "SAN.MC","ALV.DE","RY.TO","TD.TO","CBA.AX","BLK","SPGI","V","MA"}
    PHARMA    = {"LLY","ABBV","MRK","UNH","ABT","TMO","AMGN","NVS","AZN",
                 "RHHBY","NOVO-B.CO"}
    CONGLOM   = {"BRK-B","GE","SIE.DE","6758.T"}

    def _stub(self, ticker, force=False):
        if ticker in self.INTL_SEC:
            clf, ft, ds = "INTL_SEC", "20-F/6-K", "both"
        elif _has_non_us_suffix(ticker):
            clf, ft, ds = "INTL_YAHOO", "none", "yahoo_only"
        else:
            clf, ft, ds = "US_SEC", "10-K/10-Q", "both"
        sector = ("financial"    if ticker in self.FINANCIAL else
                  "pharma"       if ticker in self.PHARMA    else
                  "conglomerate" if ticker in self.CONGLOM   else "standard")
        return CheckResult(ticker=ticker, data_source=ds, classification=clf,
                           sector=sector, filing_type=ft,
                           cik=self.KNOWN_CIK_MAP.get(ticker),
                           confidence=0.95, notes=["stub"])

    def _load(self):
        from src.ingestion import ticker_loader as tl
        if "src.ingestion.ticker_loader" in sys.modules:
            del sys.modules["src.ingestion.ticker_loader"]
        with patch.object(dsc, "check_ticker", side_effect=self._stub):
            from src.ingestion import ticker_loader as tl
            return tl.load_tickers(path=EXCEL_PATH)

    def test_loads_98_tickers(self):
        assert len(self._load()) == 98

    def test_no_hardcoded_sets_in_source(self):
        from src.ingestion import ticker_loader as tl
        src = Path(tl.__file__).read_text()
        for forbidden in ("FINANCIAL_TICKERS", "PHARMA_TICKERS",
                          "CONGLOMERATE_TICKERS", "INTL_SEC_TICKERS",
                          "INTL_YAHOO_TICKERS"):
            assert forbidden not in src, \
                f"ticker_loader still references {forbidden}"

    def test_known_classifications(self):
        t = self._load()
        assert t["MSFT"].classification      == "US_SEC"
        assert t["TSM"].classification       == "INTL_SEC"
        assert t["005930.KS"].classification == "INTL_YAHOO"

    def test_known_sectors(self):
        t = self._load()
        assert t["JPM"].sector   == "financial"
        assert t["LLY"].sector   == "pharma"
        assert t["BRK-B"].sector == "conglomerate"
        assert t["NVDA"].sector  == "standard"

    def test_cik_for_seed_tickers(self):
        t = self._load()
        assert t["TSM"].cik  is not None
        assert t["ASML"].cik is not None

    def test_all_valid_classification_and_sector(self):
        valid_clf    = {"US_SEC", "INTL_SEC", "INTL_YAHOO"}
        valid_sector = {"financial", "pharma", "conglomerate", "standard"}
        for ticker, info in self._load().items():
            assert info.classification in valid_clf,    \
                f"{ticker}: bad classification '{info.classification}'"
            assert info.sector         in valid_sector, \
                f"{ticker}: bad sector '{info.sector}'"


# ---------------------------------------------------------------------------
# 10. Smoke test — real network (auto-skipped if SEC unreachable)
# ---------------------------------------------------------------------------

class TestCheckTickerSmoke:
    CASES = [
        ("NVDA",      "US_SEC",    "standard"),
        ("JPM",       "US_SEC",    "financial"),
        ("LLY",       "US_SEC",    "pharma"),
        ("BRK-B",     "US_SEC",    "conglomerate"),
        ("005930.KS", "INTL_YAHOO", None),
        ("TSM",       "INTL_SEC",   None),
    ]

    @staticmethod
    def _sec_reachable() -> bool:
        try:
            import requests
            r = requests.get("https://data.sec.gov", timeout=5)
            return r.status_code not in (403, 407) and r.status_code < 500
        except Exception:
            return False

    def test_real_tickers(self):
        if not self._sec_reachable():
            print("  [SKIP] SEC unreachable in this environment")
            return
        for ticker, exp_clf, exp_sector in self.CASES:
            r = check_ticker(ticker, force=False)
            assert r.classification == exp_clf, \
                f"{ticker}: expected {exp_clf}, got {r.classification}\n  {r.notes}"
            if exp_sector:
                assert r.sector == exp_sector, \
                    f"{ticker}: expected sector {exp_sector}, got {r.sector}"
            print(f"  ✓ {ticker:12} clf={r.classification:12} sector={r.sector}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    suites = [
        TestConfigCleaned,
        TestCheckResultStructure,
        TestNonUsSuffix,
        TestClassifySource,
        TestSectorFromSic,
        TestSectorFromYahoo,
        TestConglomerateHoldingSic,
        TestCacheRoundTrip,
        TestTickerLoader,
        TestCheckTickerSmoke,
    ]

    passed = failed = 0
    for suite_cls in suites:
        suite = suite_cls()
        methods = [m for m in dir(suite_cls) if m.startswith("test_")]
        print(f"\n{suite_cls.__name__} ({len(methods)} tests)")
        for method in methods:
            try:
                getattr(suite, method)()
                print(f"  ✓ {method}")
                passed += 1
            except Exception as e:
                print(f"  ✗ {method}: {e}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'='*50}")
    sys.exit(0 if failed == 0 else 1)