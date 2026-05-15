"""Automatic ticker classification via SEC EDGAR submissions + Yahoo Finance.

For each ticker this module determines:
    data_source  : "sec" | "yahoo_only" | "both"
    classification: "US_SEC" | "INTL_SEC" | "INTL_YAHOO"
    sector       : "financial" | "pharma" | "standard"
    filing_type  : "10-K/10-Q" | "20-F/6-K" | "none"
    cik          : str | None

Results are cached (namespace "checker", TTL 30 days).

CIK resolution cascade (4 steps):
    1. TICKER_TO_CIK seed map  — manual escape hatch
    2. edgartools Company(ticker) — fast path for US tickers
    3. find_company(excel name)  — primary for international tickers
    4. find_company(yahoo name)  — lazy fallback
    5. EDGAR full-text search    — last resort
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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config import SEC_USER_AGENT, TICKER_TO_CIK, YEARS_BACK
from src.cache.cache_manager import cache

# ---------------------------------------------------------------------------
# SIC code ranges
# ---------------------------------------------------------------------------
_SIC_FINANCIAL   = range(6000, 7000)
_SIC_PHARMA_LOW  = range(2830, 2837)
_SIC_PHARMA_HIGH = range(8000, 8100)

_YF_FINANCIAL_SECTORS = {"Financial Services", "Financials", "Insurance", "Banks"}
_YF_PHARMA_SECTORS    = {"Healthcare", "Health Care"}
_YF_PHARMA_INDUSTRIES = {
    "Drug Manufacturers—General", "Drug Manufacturers—Specialty & Generic",
    "Drug Manufacturers - General", "Drug Manufacturers - Specialty & Generic",
    "Biotechnology", "Diagnostics & Research", "Medical Devices",
    "Pharmaceutical Retailers", "Health Information Services",
    "Healthcare Plans", "Medical Instruments & Supplies",
}

_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


@dataclass
class CheckResult:
    ticker:         str
    data_source:    str
    classification: str
    sector:         str
    filing_type:    str
    cik:            Optional[str]
    confidence:     float
    notes:          list


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_ticker(ticker: str, force: bool = False, hint_name: str = "") -> CheckResult:
    """Return a CheckResult for *ticker*, cached for 30 days.

    hint_name: full company name from the input spreadsheet — used as the
               primary EDGAR search term before falling back to Yahoo Finance.
               Persisted in cache so re-runs after expiry don't need it re-supplied.
    """
    cache_key = f"check_{ticker}"
    hint_key  = f"hint_{ticker}"

    if not force:
        cached = cache.get("checker", cache_key)
        if cached:
            return CheckResult(**cached)

    # Use caller-supplied hint, or fall back to the one saved from a prior run
    effective_hint = hint_name.lower().strip()
    if not effective_hint:
        effective_hint = cache.get("checker", hint_key) or ""

    # Persist the hint so future re-runs (e.g. CLI, cache expiry) can reuse it
    if effective_hint:
        cache.set("checker", hint_key, effective_hint)

    result = _run_checks(ticker, hint_name=effective_hint)
    cache.set("checker", cache_key, asdict(result))
    return result


def check_all(tickers: list[str], force: bool = False) -> dict[str, CheckResult]:
    return {t: check_ticker(t, force=force) for t in tickers}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _run_checks(ticker: str, hint_name: str = "") -> CheckResult:
    notes: list = []
    cik = _resolve_cik(ticker, hint_name, notes)
    submissions = _fetch_submissions(cik, notes) if cik else None
    data_source, classification, filing_type = _classify_source(ticker, cik, submissions, notes)
    sic_code = _extract_sic(submissions) if submissions else None
    sector, confidence = _classify_sector(ticker, sic_code, submissions, notes)
    return CheckResult(
        ticker=ticker, data_source=data_source, classification=classification,
        sector=sector, filing_type=filing_type, cik=cik,
        confidence=confidence, notes=notes,
    )


# ---------------------------------------------------------------------------
# CIK resolution
# ---------------------------------------------------------------------------

def _resolve_cik(ticker: str, hint_name: str, notes: list) -> Optional[str]:
    # 1. Seed map
    if ticker in TICKER_TO_CIK:
        cik = TICKER_TO_CIK[ticker]
        notes.append(f"CIK from seed map: {cik}")
        return cik

    # 2. edgartools Company(ticker) — fast path
    try:
        import edgar
        edgar.set_identity(os.getenv("SEC_USER_AGENT") or SEC_USER_AGENT)
        company = edgar.Company(ticker)
        if company and company.cik:
            cik = str(company.cik).zfill(10)
            if _cik_name_matches(cik, hint_name, notes):
                notes.append(f"CIK via edgartools: {cik}")
                return cik
    except Exception as e:
        notes.append(f"edgartools CIK lookup failed: {e}")

    # 3. find_company(excel name) — primary for international tickers
    if hint_name:
        cik = _find_cik_by_name(hint_name, hint_name, notes, label="Excel name")
        if cik:
            return cik

    # 4. find_company(yahoo name) — lazy fallback
    yahoo_name = _get_yahoo_company_name(ticker)
    if yahoo_name and yahoo_name != hint_name:
        cik = _find_cik_by_name(yahoo_name, yahoo_name, notes, label="Yahoo name")
        if cik:
            return cik

    # 5. Full-text search — last resort
    ref = hint_name or yahoo_name
    if ref:
        cik = _fulltext_search(ref, notes)
        if cik:
            return cik

    notes.append("No CIK found — will use Yahoo Finance only")
    return None


def _get_yahoo_company_name(ticker: str) -> str:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return (info.get("longName") or info.get("shortName") or "").lower()
    except Exception:
        return ""


def _find_cik_by_name(name: str, ref_name: str, notes: list, label: str = "") -> Optional[str]:
    """Search EDGAR company tickers DataFrame (fast, in-memory — avoids edgartools find_company iterator bug)."""
    try:
        from edgar.reference.tickers import get_company_tickers
        name_clean = re.sub(
            r'\b(a/s|a\.s\.|ag|se|plc|ltd|inc|corp|n\.v\.|s\.a\.|s\.a|sa|spa|nv)\b.*',
            '', name.lower()
        ).strip().rstrip(',').split(",")[0].strip()
        if not name_clean or len(name_clean) < 4:
            return None
        notes.append(f"Trying company tickers search ({label}): '{name_clean}'")
        df = get_company_tickers()
        matches = df[df['company'].str.lower().str.contains(re.escape(name_clean), na=False)]
        for _, row in matches.iterrows():
            cik_val = row['cik']
            if not cik_val:
                continue
            cik = str(int(cik_val)).zfill(10)
            if _cik_name_matches(cik, ref_name or name.lower(), notes):
                notes.append(f"CIK via company tickers ({label}): {cik}")
                return cik
    except Exception as e:
        notes.append(f"company tickers search ({label}) failed: {e}")
    return None


def _cik_name_matches(cik: str, ref_name: str, notes: list) -> bool:
    """True if the EDGAR entity name for *cik* is consistent with *ref_name*."""
    if not ref_name:
        return True
    try:
        resp = _sec_get(_SEC_SUBMISSIONS_URL.format(cik=cik))
        if not resp:
            return True
        edgar_name = (resp.get("name") or "").lower()
        skip = {"the", "a", "an", "of", "and", "&"}
        words = [w for w in re.split(r'\W+', ref_name) if w and w not in skip]
        if not words:
            return True

        def _wb(w: str) -> bool:
            return bool(re.search(r'\b' + re.escape(w) + r'\b', edgar_name))

        long_words = [w for w in words if len(w) >= 4]
        if len(long_words) >= 2:
            if _wb(long_words[0]) and _wb(long_words[1]):
                return True
            notes.append(f"CIK {cik} rejected: EDGAR name '{edgar_name}' doesn't match '{ref_name[:40]}'")
            return False
        if long_words:
            if _wb(long_words[0]):
                return True
            notes.append(f"CIK {cik} rejected: EDGAR name '{edgar_name}' doesn't match '{ref_name[:40]}'")
            return False
        return True
    except Exception:
        return True


def _fulltext_search(ref_name: str, notes: list) -> Optional[str]:
    """Last-resort: EDGAR full-text filing search by company name."""
    import urllib.parse
    name_clean = re.sub(
        r'\b(a/s|a\.s\.|ag|se|plc|ltd|inc|corp|n\.v\.|sa|spa)\b.*', '', ref_name
    ).strip().split(",")[0].strip()
    if not name_clean or len(name_clean) < 4:
        return None
    notes.append(f"Trying EDGAR full-text search: '{name_clean}'")
    encoded = urllib.parse.quote(name_clean)
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{encoded}%22&forms=20-F,10-K&dateRange=custom&startdt=2020-01-01"
    try:
        resp = _sec_get(url)
        if not resp:
            return None
        hits = resp.get("hits", {}).get("hits", [])
        # Prefer 20-F filers first
        for hit in hits[:10]:
            src = hit.get("_source", {})
            if "20-F" not in str(src.get("file_type", "")):
                continue
            cik_raw = src.get("entity_id") or (src.get("ciks") or [None])[0]
            if cik_raw:
                cik = str(cik_raw).zfill(10)
                if _cik_name_matches(cik, ref_name, notes):
                    notes.append(f"CIK via full-text search (20-F): {cik}")
                    return cik
        for hit in hits[:10]:
            src = hit.get("_source", {})
            cik_raw = src.get("entity_id") or (src.get("ciks") or [None])[0]
            if cik_raw:
                cik = str(cik_raw).zfill(10)
                if _cik_name_matches(cik, ref_name, notes):
                    notes.append(f"CIK via full-text search: {cik}")
                    return cik
    except Exception as e:
        notes.append(f"EDGAR full-text search failed: {e}")
    return None


# ---------------------------------------------------------------------------
# SEC submissions
# ---------------------------------------------------------------------------

def _fetch_submissions(cik: str, notes: list) -> Optional[dict]:
    data = _sec_get(_SEC_SUBMISSIONS_URL.format(cik=cik))
    if data:
        notes.append(f"Submissions fetched for CIK {cik}")
        return data
    notes.append(f"Submissions fetch failed for CIK {cik}")
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_source(
    ticker: str, cik: Optional[str], submissions: Optional[dict], notes: list
) -> tuple[str, str, str]:
    if not cik or not submissions:
        if _has_non_us_suffix(ticker):
            notes.append("Non-US ticker suffix → INTL_YAHOO")
        else:
            notes.append("No CIK found → INTL_YAHOO")
        return "yahoo_only", "INTL_YAHOO", "none"

    recent_forms = _get_recent_forms(submissions)
    has_10k = bool(recent_forms & {"10-K"})
    has_10q = bool(recent_forms & {"10-Q"})
    has_20f = bool(recent_forms & {"20-F"})
    has_40f = bool(recent_forms & {"40-F"})
    has_6k  = bool(recent_forms & {"6-K"})

    if has_10k or has_10q:
        notes.append(f"US SEC filer (forms found: {recent_forms & {'10-K','10-Q'}})")
        return "both", "US_SEC", "10-K/10-Q"
    if has_20f or has_40f or has_6k:
        intl_forms = recent_forms & {"20-F", "40-F", "6-K"}
        notes.append(f"International SEC filer (forms found: {intl_forms})")
        return "both", "INTL_SEC", "20-F/6-K"

    notes.append(f"CIK exists ({cik}) but no 10-K/10-Q/20-F/40-F/6-K found → INTL_YAHOO")
    return "yahoo_only", "INTL_YAHOO", "none"


def _get_recent_forms(submissions: dict, years: int = YEARS_BACK) -> set:
    """Return form types filed within the last *years* years only."""
    try:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=years * 365)).isoformat()
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        return {f.upper().strip() for f, d in zip(forms, dates) if d >= cutoff}
    except Exception:
        return set()


def _has_non_us_suffix(ticker: str) -> bool:
    non_us = {".KS",".HK",".PA",".SW",".DE",".L",".TO",".AX",".MC",".CO",".T",
              ".AS",".BR",".LS",".MI",".ST",".HE",".OL"}
    return any(ticker.upper().endswith(s) for s in non_us)


def _extract_sic(submissions: dict) -> Optional[int]:
    try:
        return int(submissions["sic"]) if submissions.get("sic") is not None else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Sector classification
# ---------------------------------------------------------------------------

def _classify_sector(
    ticker: str, sic_code: Optional[int], submissions: Optional[dict], notes: list
) -> tuple[str, float]:
    yf_sector, yf_industry = _fetch_yahoo_sector(ticker, notes)

    if sic_code is not None and sic_code in _SIC_PHARMA_LOW:
        notes.append(f"SIC {sic_code} → pharma")
        return "pharma", 0.95

    if yf_sector in _YF_PHARMA_SECTORS:
        if yf_industry in _YF_PHARMA_INDUSTRIES:
            notes.append(f"Yahoo sector '{yf_sector}' + industry '{yf_industry}' → pharma")
            return "pharma", 0.85
        notes.append(f"Yahoo sector '{yf_sector}' but industry '{yf_industry}' → standard")
        return "standard", 0.70

    if sic_code is not None:
        if sic_code in _SIC_FINANCIAL:
            notes.append(f"SIC {sic_code} → financial")
            return "financial", 0.95
        notes.append(f"SIC {sic_code} → using as secondary signal only")

    if yf_sector in _YF_FINANCIAL_SECTORS:
        notes.append(f"Yahoo sector '{yf_sector}' → financial")
        return "financial", 0.85

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


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _sec_get(url: str, retries: int = 3) -> Optional[dict]:
    headers = {"User-Agent": os.getenv("SEC_USER_AGENT") or SEC_USER_AGENT,
               "Accept-Encoding": "gzip, deflate"}
    for attempt in range(retries):
        try:
            time.sleep(0.13)
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
            elif resp.status_code == 404:
                return None
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# CLI
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
        print(f"  classification: {r.classification}  |  CIK: {r.cik}")
        print(f"  sector: {r.sector} ({r.confidence:.0%})  |  filing: {r.filing_type}")
        for note in r.notes:
            print(f"  • {note}")
