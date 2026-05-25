"""Generate business model narratives using Claude API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import LLM_MAX_TOKENS
from src.cache.cache_manager import cache
from src.cache.cache_keys import dict_hash
from src.analysis.segment_aggregator import AggregatedCompanyData
from src.llm.provider import complete_text, get_active_provider


_SYSTEM = """You are a senior equity research analyst writing professional business model summaries.
Write clear, factual analysis based strictly on the financial data provided.
Do not mention stock prices, valuations, or investment recommendations.
Format your response in clean Markdown with section headers."""

_USER_TEMPLATE = """Write a professional business model analysis for {name} ({ticker}).

Use this financial data covering the last 3 years:

{data_json}

Your analysis must cover (400-600 words total):

## Revenue Drivers
- Primary business segments and their contribution to total revenue
- Revenue mix and how it has evolved over the past 3 years
- Key products, services, or geographies driving growth

## Earnings & Profitability
- Gross margin and operating margin by segment (where available)
- Major cost drivers (R&D, SG&A, COGS)
- Operating leverage and profitability trends

## Business Model Summary
- How the company converts revenue into earnings
- Competitive positioning visible from the segment structure
- Any notable shifts in segment mix or margin profile

Be specific and quantitative — reference actual revenue figures and percentages from the data."""


def write_business_model(agg: AggregatedCompanyData) -> str:
    """
    Use Claude to write a business model narrative.
    Cached by hash of the aggregated segment data.
    Returns Markdown text.
    """
    # Build a compact summary for the prompt
    summary = _build_data_summary(agg)
    provider  = get_active_provider()
    data_hash = dict_hash(summary)
    cache_key = f"narrative_{agg.ticker}_{provider}_{data_hash}"

    cached = cache.get("llm", cache_key)
    if cached:
        return cached.get("text", "")

    try:
        text = complete_text(
            system = _SYSTEM,
            user   = _USER_TEMPLATE.format(
                name     = agg.name,
                ticker   = agg.ticker,
                data_json= json.dumps(summary, indent=2, default=str),
            ),
        )
    except Exception as e:
        text = f"# {agg.name} ({agg.ticker})\n\n*Analysis generation error ({provider}): {e}*"

    cache.set("llm", cache_key, {"text": text})
    return text


def _build_data_summary(agg: AggregatedCompanyData) -> dict:
    """Build a compact JSON summary for the LLM prompt."""
    def _fmt_millions(val):
        if val is None:
            return None
        return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.0f}M"

    annual_summaries = []
    for sd in agg.annual_periods[:3]:  # last 3 annual reports
        entry = {
            "period":          sd.period,
            "total_revenue":   _fmt_millions(sd.total_revenue),
            "gross_profit":    _fmt_millions(sd.gross_profit),
            "operating_income":_fmt_millions(sd.operating_income),
            "net_income":      _fmt_millions(sd.net_income),
            "rd_expense":      _fmt_millions(sd.rd_expense),
            "sga_expense":     _fmt_millions(sd.sga_expense),
            "segments":        [
                {"name": s.segment_name, "revenue": _fmt_millions(s.value)}
                for s in sd.segments
            ],
        }
        if sd.total_revenue and sd.gross_profit:
            entry["gross_margin_pct"] = f"{sd.gross_profit/sd.total_revenue*100:.1f}%"
        if sd.total_revenue and sd.operating_income:
            entry["operating_margin_pct"] = f"{sd.operating_income/sd.total_revenue*100:.1f}%"
        annual_summaries.append(entry)

    return {
        "ticker":         agg.ticker,
        "name":           agg.name,
        "ttm_revenue":    _fmt_millions(agg.ttm_revenue),
        "annual_reports": annual_summaries,
        "segment_trend":  {
            seg: [
                {"period": p["period"], "revenue": _fmt_millions(p["value"])}
                for p in pts
            ]
            for seg, pts in list(agg.segment_trend.items())[:10]  # top 10 segments
        },
    }
