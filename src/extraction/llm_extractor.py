"""LLM-based segment extraction from filing note text.

P&L comes from edgartools XBRL; this module only extracts segment breakdowns
(name + revenue). Input: ~5–20 KB of segment-note text. Output: SegmentValue list.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.cache.cache_manager import cache
from src.cache.cache_keys import sha256_short
from src.extraction.models import SegmentValue
from src.llm.provider import complete_json, get_active_provider


_SEGMENT_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "segment_name":        {"type": "string"},
        "revenue":             {"type": ["number", "null"]},
        "operating_income":    {"type": ["number", "null"]},
        "net_interest_income": {"type": ["number", "null"]},
        "segment_type":        {"type": ["string", "null"]},
    },
    "required": ["segment_name", "revenue", "operating_income",
                 "net_interest_income", "segment_type"],
}

SEGMENTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "currency": {"type": "string"},
        "segments": {"type": "array", "items": _SEGMENT_ITEM_SCHEMA},
        "notes":    {"type": "array", "items": {"type": "string"}},
    },
    "required": ["currency", "segments", "notes"],
}

_SYSTEM_STANDARD = """You extract reportable business segments from SEC filing note text.
Rules:
- All monetary values in MILLIONS of reporting currency.
- Only extract segments EXPLICITLY stated. Never invent or estimate.
- `revenue` = segment net sales / segment revenue.
- `operating_income` = segment operating income if disclosed (else null).
- Use null for any unknown field."""

_SYSTEM_FINANCIAL = """You extract reportable segments from filings of banks and insurance companies.
For each segment, populate `net_interest_income` if disclosed (typical segments: Consumer Banking,
Investment Banking, Wealth Management, Trading, Corporate). `revenue` should be total segment revenue.
All values in MILLIONS. Use null for missing fields."""

_SYSTEM_PHARMA = """You extract segments for pharma/biotech companies, organized by therapeutic area,
drug product, geography, or business line. Set `segment_type` to exactly one of:
"therapeutic_area", "product", "geography", or "business_line".
All values in MILLIONS. Use null for missing fields."""


def _get_system_prompt(sector: str) -> str:
    if sector == "financial": return _SYSTEM_FINANCIAL
    if sector == "pharma":    return _SYSTEM_PHARMA
    return _SYSTEM_STANDARD


def extract_segments(
    note_text:       str,
    ticker:          str,
    period:          str,
    sector:          str = "standard",
    known_total_rev: Optional[float] = None,
) -> List[SegmentValue]:
    """LLM-extract segment breakdown from note text. Returns [] on failure."""
    if not note_text or len(note_text.strip()) < 100:
        return []

    provider  = get_active_provider()
    text_hash = sha256_short(note_text, prefix_chars=5000)
    cache_key = f"seg_{ticker}_{period}_{sector}_{provider}_{text_hash}"

    cached = cache.get("llm", cache_key)
    if cached is not None:
        return [SegmentValue(**s) for s in cached]

    rev_hint = ""
    if known_total_rev:
        rev_hint = (f"\nKnown total revenue for {period}: ~${known_total_rev/1e6:.0f}M. "
                    "Segment revenues should sum to approximately this.")

    user_prompt = (
        f"Extract business segments for {ticker} ({period}) from the following "
        f"segment-information note.{rev_hint}\n\n---\n\n{note_text}"
    )

    try:
        data = complete_json(
            system = _get_system_prompt(sector),
            user   = user_prompt,
            schema = SEGMENTS_SCHEMA,
        )
    except Exception:
        return []

    currency = data.get("currency", "USD")
    segments: List[SegmentValue] = []
    for seg in data.get("segments", []) or []:
        rev = seg.get("revenue") or seg.get("net_interest_income")
        if rev is None:
            continue
        value_usd = _scale_to_usd(rev)
        if value_usd is None:
            continue
        segments.append(SegmentValue(
            segment_name = str(seg.get("segment_name", "Unknown")),
            value        = value_usd,
            unit         = currency,
            period       = period,
            concept      = "llm_extracted",
            is_annual    = period.startswith("FY"),
        ))

    cache.set("llm", cache_key, [vars(s) for s in segments])
    return segments


def _scale_to_usd(v: Optional[float]) -> Optional[float]:
    """Input in MILLIONS; return absolute units. Corrects common mis-reporting."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if abs(v) > 1e12:       # accidentally in full units
        v = v / 1e6
    elif abs(v) < 1 and v != 0:  # accidentally in billions
        v = v * 1e3
    return v * 1e6
