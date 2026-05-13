"""Automatic ticker classification via SEC EDGAR submissions + Yahoo Finance.

For each ticker this module determines:
    data_source  : "sec" | "yahoo_only" | "both"
    classification: "US_SEC" | "INTL_SEC" | "INTL_YAHOO"
    sector       : "financial" | "pharma" | "conglomerate" | "standard"
    filing_type  : "10-K/10-Q" | "20-F/6-K" | "none"
    cik          : str | None

All results are cached (namespace "checker", TTL 30 days) so the live API
is only hit once per ticker per month.

No hardcoded ticker lists are used for classification logic.
The only hardcoded data is the TICKER_TO_CIK seed map (CIK lookup fallback
for a handful of known international filers whose ADR ticker isn't directly
searchable via edgartools).
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import requests

# ── Path setup: add project root so config.py and src/ are importable ────────
# File lives at <root>/src/ingestion/data_source_checker.py
# Root = Path(__file__).parent.parent.parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config import SEC_USER_AGENT, TICKER_TO_CIK
from src.cache.cache_manager import cache

# ---------------------------------------------------------------------------
# SIC code ranges that define sector buckets
# Source: SEC EDGAR SIC code directory
# ---------------------------------------------------------------------------
_SIC_FINANCIAL   = range(6000, 7000)   # 6000-6999: Finance, Insurance, Real Estate
_SIC_PHARMA_LOW  = range(2830, 2837)   # 2830-2836: Drugs
_SIC_PHARMA_HIGH = range(8000, 8100)   # 8000-8099: Health Services (for biotech edge cases)

# Yahoo Finance sector strings that map to our sector buckets
_YF_FINANCIAL_SECTORS = {"Financial Services", "Financials", "Insurance", "Banks"}
_YF_PHARMA_SECTORS    = {"Healthcare", "Health Care"}
_YF_PHARMA_INDUSTRIES = {
    "Drug Manufacturers—General", "Drug Manufacturers—Specialty & Generic",
    "Biotechnology", "Diagnostics & Research", "Medical Devices",
    "Pharmaceutical Retailers", "Health Information Services",
}

# Yahoo Finance industry strings that indicate conglomerate regardless of sector
_YF_CONGLOMERATE_INDUSTRIES = {
    "Conglomerates", "Diversified Industrials", "Multi-Sector Holdings",
    "Insurance - Diversified",   # BRK-B: Yahoo uses hyphen
    "Insurance—Diversified",     # em dash fallback
}

# Industry keywords used as a secondary signal for conglomerate detection
_CONGLOMERATE_KEYWORDS = {
    "conglomerate", "diversified industrials", "multi-sector", "holding",
    "multi-industry", "industrial conglomerate",
}

_CONGLOMERATE_SEGMENT_THRESHOLD = 5

_ANNUAL_FORMS    = {"10-K", "20-F"}
_QUARTERLY_FORMS = {"10-Q", "6-K"}

_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_SEC_COMPANY_SEARCH  = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K,20-F"


@dataclass
class CheckResult:
    ticker:         str
    data_source:    str            # "sec" | "yahoo_only" | "both"
    classification: str            # "US_SEC" | "INTL_SEC" | "INTL_YAHOO"
    sector:         str            # "financial" | "pharma" | "conglomerate" | "standard"
    filing_type:    str            # "10-K/10-Q" | "20-F/6-K" | "none"
    cik:            Optional[str]
    confidence:     float          # 0.0-1.0 how sure we are about sector
    notes:          list           # audit trail of signals used


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_ticker(ticker: str, force: bool = False) -> CheckResult:
    """Return a fully-populated CheckResult for *ticker*.

    Results are cached for 30 days.  Pass force=True to bypass cache.
    """
    cache_key = f"check_{ticker}"
    if not force:
        cached = cache.get("checker", cache_key)
        if cached:
            return CheckResult(**cached)

    result = _run_checks(ticker)
    cache.set("checker", cache_key, asdict(result))
    return result


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _run_checks(ticker: str) -> CheckResult:
    notes: list = []

    cik = _resolve_cik(ticker, notes)
    submissions = _fetch_submissions(cik, notes) if cik else None
    data_source, classification, filing_type = _classify_source(
        ticker, cik, submissions, notes
    )
    sic_code = _extract_sic(submissions) if submissions else None
    sector, sector_confidence = _classify_sector(
        ticker, sic_code, submissions, notes
    )

    return CheckResult(
        ticker         = ticker,
        data_source    = data_source,
        classification = classification,
        sector         = sector,
        filing_type    = filing_type,
        cik            = cik,
        confidence     = sector_confidence,
        notes          = notes,
    )


# ---------------------------------------------------------------------------
# CIK resolution
# ---------------------------------------------------------------------------

def _resolve_cik(ticker: str, notes: list) -> Optional[str]:
    if ticker in TICKER_TO_CIK:
        cik = TICKER_TO_CIK[ticker]
        notes.append(f"CIK from seed map: {cik}")
        return cik

    try:
        import edgar
        edgar.set_identity(os.getenv("SEC_USER_AGENT") or SEC_USER_AGENT)
        company = edgar.Company(ticker)
        if company and company.cik:
            cik = str(company.cik).zfill(10)
            notes.append(f"CIK via edgartools: {cik}")
            return cik
    except Exception as e:
        notes.append(f"edgartools CIK lookup failed: {e}")

    cik = _search_edgar_cik(ticker, notes)
    if cik:
        return cik

    notes.append("No CIK found — will use Yahoo Finance only")
    return None


def _search_edgar_cik(ticker: str, notes: list) -> Optional[str]:
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=10-K,20-F&dateRange=custom&startdt=2018-01-01"
    try:
        resp = _sec_get(url)
        if not resp:
            return None
        hits = resp.get("hits", {}).get("hits", [])
        if hits:
            entity = hits[0].get("_source", {})
            cik_raw = entity.get("entity_id") or entity.get("ciks", [None])[0]
            if cik_raw:
                cik = str(cik_raw).zfill(10)
                notes.append(f"CIK via EDGAR full-text search: {cik}")
                return cik
    except Exception as e:
        notes.append(f"EDGAR CIK search failed: {e}")
    return None


# ---------------------------------------------------------------------------
# SEC submissions fetch
# ---------------------------------------------------------------------------

def _fetch_submissions(cik: str, notes: list) -> Optional[dict]:
    url = _SEC_SUBMISSIONS_URL.format(cik=cik)
    try:
        data = _sec_get(url)
        if data:
            notes.append(f"Submissions fetched for CIK {cik}")
            return data
    except Exception as e:
        notes.append(f"Submissions fetch failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Source / classification / filing-type detection
# ---------------------------------------------------------------------------

def _classify_source(
    ticker: str,
    cik: Optional[str],
    submissions: Optional[dict],
    notes: list,
) -> tuple[str, str, str]:
    if not cik or not submissions:
        if _has_non_us_suffix(ticker):
            notes.append("Non-US ticker suffix → INTL_YAHOO")
            return "yahoo_only", "INTL_YAHOO", "none"
        notes.append("No CIK found, no non-US suffix → treating as INTL_YAHOO (best guess)")
        return "yahoo_only", "INTL_YAHOO", "none"

    recent_forms = _get_recent_forms(submissions)
    has_10k = bool(recent_forms & {"10-K"})
    has_10q = bool(recent_forms & {"10-Q"})
    has_20f = bool(recent_forms & {"20-F"})
    has_6k  = bool(recent_forms & {"6-K"})

    if has_10k or has_10q:
        notes.append(f"US SEC filer (forms found: {recent_forms & {'10-K','10-Q'}})")
        return "both", "US_SEC", "10-K/10-Q"

    if has_20f or has_6k:
        notes.append(f"International SEC filer (forms found: {recent_forms & {'20-F','6-K'}})")
        return "both", "INTL_SEC", "20-F/6-K"

    notes.append(f"CIK exists ({cik}) but no 10-K/10-Q/20-F/6-K found → yahoo_only")
    if _has_non_us_suffix(ticker):
        return "yahoo_only", "INTL_YAHOO", "none"
    return "yahoo_only", "INTL_YAHOO", "none"


def _get_recent_forms(submissions: dict) -> set:
    forms = set()
    try:
        recent = submissions.get("filings", {}).get("recent", {})
        for form in (recent.get("form") or []):
            forms.add(form.upper().strip())
    except Exception:
        pass
    return forms


def _has_non_us_suffix(ticker: str) -> bool:
    non_us_suffixes = {
        ".KS", ".HK", ".PA", ".SW", ".DE", ".L",
        ".TO", ".AX", ".MC", ".CO", ".T", ".AS",
        ".BR", ".LS", ".MI", ".ST", ".HE", ".OL",
    }
    return any(ticker.upper().endswith(s) for s in non_us_suffixes)


# ---------------------------------------------------------------------------
# SIC extraction
# ---------------------------------------------------------------------------

def _extract_sic(submissions: dict) -> Optional[int]:
    try:
        sic_raw = submissions.get("sic")
        if sic_raw is not None:
            return int(sic_raw)
    except (ValueError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Sector classification
# ---------------------------------------------------------------------------

def _classify_sector(
    ticker: str,
    sic_code: Optional[int],
    submissions: Optional[dict],
    notes: list,
) -> tuple[str, float]:
    # ── Yahoo industry conglomerate check FIRST ───────────────────────────────
    # Must run before SIC financial range because some conglomerates (e.g. BRK-B)
    # file under insurance SIC (6331) which would otherwise return "financial".
    yf_sector, yf_industry = _fetch_yahoo_sector(ticker, notes)

    if yf_industry in _YF_CONGLOMERATE_INDUSTRIES:
        notes.append(f"Yahoo industry '{yf_industry}' → conglomerate")
        return "conglomerate", 0.90

    # ── SIC-based classification ──────────────────────────────────────────────
    if sic_code is not None:
        if sic_code in _SIC_FINANCIAL:
            notes.append(f"SIC {sic_code} → financial")
            return "financial", 0.95
        if sic_code in _SIC_PHARMA_LOW:
            notes.append(f"SIC {sic_code} → pharma")
            return "pharma", 0.95
        notes.append(f"SIC {sic_code} → using as secondary signal only")

    # ── Yahoo sector fallback ─────────────────────────────────────────────────
    if yf_sector in _YF_FINANCIAL_SECTORS:
        notes.append(f"Yahoo sector '{yf_sector}' → financial")
        return "financial", 0.85

    if yf_sector in _YF_PHARMA_SECTORS:
        if yf_industry in _YF_PHARMA_INDUSTRIES:
            notes.append(f"Yahoo sector '{yf_sector}' + industry '{yf_industry}' → pharma")
            return "pharma", 0.85
        notes.append(f"Yahoo sector '{yf_sector}' but industry '{yf_industry}' → standard")
        return "standard", 0.70

    if _is_conglomerate(yf_industry, submissions, notes):
        return "conglomerate", 0.75

    notes.append("No strong sector signal → standard")
    return "standard", 0.60


def _fetch_yahoo_sector(ticker: str, notes: list) -> tuple[str, str]:
    cache_key = f"yf_info_{ticker}"
    cached = cache.get("checker", cache_key)
    if cached:
        return cached.get("sector", ""), cached.get("industry", "")

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        sector   = info.get("sector", "") or ""
        industry = info.get("industry", "") or ""
        cache.set("checker", cache_key, {"sector": sector, "industry": industry})
        notes.append(f"Yahoo Finance: sector='{sector}', industry='{industry}'")
        return sector, industry
    except Exception as e:
        notes.append(f"Yahoo Finance info fetch failed: {e}")
        return "", ""


def _is_conglomerate(
    yf_industry: str,
    submissions: Optional[dict],
    notes: list,
) -> bool:
    if yf_industry:
        lower = yf_industry.lower()
        for kw in _CONGLOMERATE_KEYWORDS:
            if kw in lower:
                notes.append(f"Conglomerate keyword '{kw}' in industry '{yf_industry}'")
                return True

    if submissions:
        sic = _extract_sic(submissions)
        if sic in (6719, 6726):
            notes.append(f"SIC {sic} (holding company) → conglomerate")
            return True

    return False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _sec_get(url: str, retries: int = 3) -> Optional[dict]:
    headers = {
        "User-Agent": os.getenv("SEC_USER_AGENT") or SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }
    for attempt in range(retries):
        try:
            time.sleep(0.13)
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 404:
                return None
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------

def check_all(tickers: list[str], force: bool = False) -> dict[str, CheckResult]:
    return {t: check_ticker(t, force=force) for t in tickers}


# ---------------------------------------------------------------------------
# CLI diagnostic
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check ticker classification")
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    for t in args.tickers:
        r = check_ticker(t, force=args.force)
        print(f"\n{r.ticker}")
        print(f"  data_source   : {r.data_source}")
        print(f"  classification: {r.classification}")
        print(f"  sector        : {r.sector}  (confidence {r.confidence:.0%})")
        print(f"  filing_type   : {r.filing_type}")
        print(f"  CIK           : {r.cik}")
        for note in r.notes:
            print(f"  • {note}")