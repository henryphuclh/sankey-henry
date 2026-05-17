"""Generate short LLM explanations for missing segment or quarterly coverage."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.cache.cache_manager import cache
from src.cache.cache_keys import dict_hash
from src.analysis.segment_aggregator import AggregatedCompanyData
from src.llm.provider import complete_text, get_active_provider
from config import QUARTERS_BACK

_QUARTERLY_THRESHOLD = QUARTERS_BACK  # mirrors config so only one place to change


def _clean_notes(notes):
    skip = ("EXPENSE_DETAIL", "SECTOR_FALLBACK", "BANK_NII", "BANK_NONINT")
    return [n for n in (notes or []) if not any(n.startswith(s) for s in skip)]


def _call_llm(cache_key: str, system: str, user: str) -> str:
    cached = cache.get("llm", cache_key)
    if cached:
        return cached.get("text", "")
    try:
        text = complete_text(system=system, user=user, use_simple=True)
    except Exception:
        text = ""
    cache.set("llm", cache_key, {"text": text})
    return text


# ── Partial annual data ───────────────────────────────────────────────────────

_ANNUAL_SYSTEM = (
    "You are a financial data analyst writing a brief professional data coverage note. "
    "Write exactly 1-2 sentences explaining why fewer than 3 annual reports are available. "
    "Use neutral, professional language. Do not mention internal tools, code, or automation. "
    "Focus on the company's listing history, spin-off date, or SEC filing availability."
)

_ANNUAL_USER = """\
Company: {name} ({ticker})
Annual reports available: {annual_count} out of 3 expected (3 fiscal years)
Data source: {classification}

Explain in 1-2 sentences why only {annual_count} annual report(s) are available."""


def generate_partial_data_note(agg: AggregatedCompanyData) -> str:
    """Return LLM explanation when fewer than 3 annual reports are available, else ''."""
    if agg.annual_count >= 3:
        return ""

    provider    = get_active_provider()
    fingerprint = {
        "ticker":       agg.ticker,
        "annual_count": agg.annual_count,
        "type":         "annual_shortage",
    }
    cache_key = f"annualnote_{agg.ticker}_{provider}_{dict_hash(fingerprint)}"

    return _call_llm(
        cache_key,
        system = _ANNUAL_SYSTEM,
        user   = _ANNUAL_USER.format(
            name           = agg.name,
            ticker         = agg.ticker,
            annual_count   = agg.annual_count,
            classification = getattr(agg, "classification", "unknown"),
        ),
    )


# ── Segment coverage gap ──────────────────────────────────────────────────────

_SEG_SYSTEM = (
    "You are a financial data analyst. Explain in 1-2 concise sentences why "
    "a company's business segment breakdown is missing or incomplete for the specific periods listed. "
    "You MUST explicitly name the periods that are missing (e.g. 'FY2023', '2024Q1', '2023Q3'). "
    "Be specific to the company's reporting practice. "
    "Do not mention any internal system, code, or tool — frame it purely as "
    "the company's disclosure practice or data source limitation."
)

_SEG_USER = """\
Company: {name} ({ticker})
Data source: {classification} (INTL_YAHOO=Yahoo only, INTL_SEC=international SEC filer, US_SEC=US SEC filer)
Sector: {sector}

Periods with missing segment data:
{gap_info}

All periods available (for context):
{all_periods_info}

Explain briefly why segment data is unavailable, naming the specific periods listed above."""


def generate_coverage_note(agg: AggregatedCompanyData) -> str:
    """1-2 sentence explanation for missing segment data (annual + quarterly), or '' if none missing."""
    all_periods = agg.annual_periods + agg.quarterly_periods
    gap_periods = [p for p in all_periods if not p.segments]
    if not gap_periods:
        return ""

    provider    = get_active_provider()
    fingerprint = {
        "ticker":         agg.ticker,
        "classification": getattr(agg, "classification", ""),
        "gap_periods":    sorted(p.period for p in gap_periods),
        "methods":        sorted({p.extraction_method for p in gap_periods}),
    }
    cache_key = f"covnote2_{agg.ticker}_{provider}_{dict_hash(fingerprint)}"

    all_periods_sorted = sorted(all_periods, key=lambda x: x.period, reverse=True)
    return _call_llm(
        cache_key,
        system = _SEG_SYSTEM,
        user   = _SEG_USER.format(
            name             = agg.name,
            ticker           = agg.ticker,
            classification   = getattr(agg, "classification", "unknown"),
            sector           = agg.sector or "standard",
            gap_info         = json.dumps([
                {"period": p.period, "type": "annual" if p.is_annual else "quarterly",
                 "extraction_method": p.extraction_method, "notes": _clean_notes(p.notes)}
                for p in sorted(gap_periods, key=lambda x: x.period, reverse=True)
            ], indent=2),
            all_periods_info = json.dumps([
                {"period": p.period, "type": "annual" if p.is_annual else "quarterly",
                 "segments": len(p.segments), "method": p.extraction_method}
                for p in all_periods_sorted
            ], indent=2),
        ),
    )


# ── Quarterly data limitation ─────────────────────────────────────────────────

_QTR_SYSTEM = (
    "You are a financial data analyst writing professional data coverage notes. "
    "Write exactly 1-2 concise sentences explaining why fewer quarterly reports than expected are available. "
    "Use neutral, professional language. Do not mention internal tools, code, or automation."
)

_QTR_USER_INTL_SEC = """\
Company: {name} ({ticker})
Quarterly reports available: {quarterly_count} out of 12 expected (4 quarters × 3 years)

Explain in 1-2 sentences following this causal chain:
1. SEC 6-K filings do not provide a standardized income statement structure, so quarterly P&L \
data is sourced from Yahoo Finance instead.
2. Yahoo Finance does not always maintain a complete 3-year quarterly history, resulting in \
only {quarterly_count} periods being available."""

_QTR_USER_YAHOO_ONLY = """\
Company: {name} ({ticker})
Quarterly reports available: {quarterly_count} out of 12 expected (4 quarters × 3 years)

This company does not file with the SEC. All financial data comes exclusively from Yahoo Finance.

Explain in 1-2 sentences why only {quarterly_count} quarterly periods are available, focusing \
on Yahoo Finance's limited historical coverage for companies without SEC filings."""


def generate_quarterly_note(agg: AggregatedCompanyData) -> str:
    """1-2 sentence explanation for limited quarterly data (INTL only), or '' if sufficient."""
    classification = getattr(agg, "classification", "")
    if classification == "US_SEC":
        return ""
    if agg.quarterly_count >= _QUARTERLY_THRESHOLD:
        return ""

    is_yahoo_only = classification == "INTL_YAHOO"
    provider    = get_active_provider()
    fingerprint = {
        "ticker":          agg.ticker,
        "quarterly_count": agg.quarterly_count,
        "classification":  classification,
    }
    cache_key = f"qtrnote2_{agg.ticker}_{provider}_{dict_hash(fingerprint)}"
    template  = _QTR_USER_YAHOO_ONLY if is_yahoo_only else _QTR_USER_INTL_SEC

    return _call_llm(
        cache_key,
        system = _QTR_SYSTEM,
        user   = template.format(
            name            = agg.name,
            ticker          = agg.ticker,
            quarterly_count = agg.quarterly_count,
        ),
    )


# ── US_SEC quarterly gap note ─────────────────────────────────────────────────

_US_QTR_THRESHOLD = QUARTERS_BACK

_US_QTR_SYSTEM = (
    "You are a financial data analyst writing a brief professional data coverage note. "
    "Write exactly 1-2 sentences explaining why not all 12 quarterly periods are available. "
    "Use neutral language. Do not mention internal tools, code, or automation. "
    "Avoid the phrases 'technical constraints', 'system', or 'automated'. "
    "Frame the explanation purely as a characteristic of SEC filing structure and "
    "Yahoo Finance data availability."
)

_US_QTR_USER = """\
Company: {name} ({ticker})
Quarterly periods available: {quarterly_count} out of 12 (4 quarters × 3 fiscal years)

Explain in 1-2 sentences using this exact causal chain:
1. SEC 10-Q quarterly filings cover only the first three fiscal quarters; the fourth fiscal quarter \
(fiscal year-end) is not filed as a standalone quarterly report and must be sourced from Yahoo Finance.
2. Yahoo Finance's quarterly income history is limited to approximately the most recent five quarters, \
so fiscal year-end quarter data for periods beyond that window is unavailable."""


def generate_us_sec_quarterly_note(agg: AggregatedCompanyData) -> str:
    """1-2 sentence note explaining US_SEC quarterly gaps, or '' if not applicable."""
    if getattr(agg, "classification", "") != "US_SEC":
        return ""
    if agg.quarterly_count >= _US_QTR_THRESHOLD:
        return ""

    provider    = get_active_provider()
    fingerprint = {
        "ticker":          agg.ticker,
        "quarterly_count": agg.quarterly_count,
        "type":            "us_sec",
    }
    cache_key = f"usqtrnote_{agg.ticker}_{provider}_{dict_hash(fingerprint)}"

    return _call_llm(
        cache_key,
        system = _US_QTR_SYSTEM,
        user   = _US_QTR_USER.format(
            name            = agg.name,
            ticker          = agg.ticker,
            quarterly_count = agg.quarterly_count,
        ),
    )
