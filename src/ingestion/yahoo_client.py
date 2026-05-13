"""Yahoo Finance client — income statements, segment data, currency conversion."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.cache.cache_manager import cache
from src.ingestion.rate_limiter import yahoo_limiter


def _safe_float(val) -> Optional[float]:
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def _df_to_dict(df: pd.DataFrame) -> Dict:
    """Convert DataFrame with DatetimeIndex columns to JSON-serializable dict."""
    if df is None or df.empty:
        return {}
    result = {}
    for col in df.columns:
        col_str = str(col)[:10]  # keep only date portion
        result[col_str] = {str(k): _safe_float(v) for k, v in df[col].items()}
    return result


def get_financials(ticker: str) -> Dict[str, Any]:
    """
    Return a dict with keys:
        annual_income    : {date_str: {metric: value}}
        quarterly_income : {date_str: {metric: value}}
        info             : yf .info dict (subset)
        currency         : reporting currency
        usd_rate         : latest USD conversion rate (1 unit of currency = X USD)
    """
    cached = cache.get("yfinance", ticker)
    if cached:
        return cached

    yahoo_limiter()
    tkr = yf.Ticker(ticker)

    result: Dict[str, Any] = {
        "annual_income":    {},
        "quarterly_income": {},
        "info":             {},
        "currency":         "USD",
        "usd_rate":         1.0,
    }

    try:
        ann = tkr.income_stmt
        if ann is not None and not ann.empty:
            result["annual_income"] = _df_to_dict(ann)
    except Exception:
        pass

    try:
        qtr = tkr.quarterly_income_stmt
        if qtr is not None and not qtr.empty:
            result["quarterly_income"] = _df_to_dict(qtr)
    except Exception:
        pass

    # Collect key info fields
    try:
        info = tkr.info or {}
        result["info"] = {
            k: info.get(k) for k in (
                "longName", "sector", "industry", "longBusinessSummary",
                "financialCurrency", "totalRevenue", "grossProfits",
                "operatingIncome", "netIncomeToCommon", "country",
            )
        }
        currency = info.get("financialCurrency", "USD") or "USD"
        result["currency"] = currency
    except Exception:
        currency = "USD"

    # Currency conversion to USD
    if currency != "USD":
        result["usd_rate"] = _get_usd_rate(currency)

    cache.set("yfinance", ticker, result)
    return result


def _get_usd_rate(currency: str) -> float:
    """Return the latest exchange rate: 1 unit of `currency` in USD."""
    pair = f"{currency}USD=X"
    cached = cache.get("yfinance", f"fx_{pair}")
    if cached:
        return cached.get("rate", 1.0)
    try:
        yahoo_limiter()
        data = yf.download(pair, period="5d", auto_adjust=True, progress=False)
        if not data.empty:
            rate = float(data["Close"].iloc[-1])
            cache.set("yfinance", f"fx_{pair}", {"rate": rate})
            return rate
    except Exception:
        pass
    return 1.0


def get_total_revenue_by_period(ticker: str) -> Dict[str, Optional[float]]:
    """
    Return {period_str: total_revenue_usd} for all available annual and quarterly periods.
    Period string format: 'FY2024' or '2024Q3'.
    """
    data = get_financials(ticker)
    usd_rate = data.get("usd_rate", 1.0)
    result: Dict[str, Optional[float]] = {}

    for date_str, metrics in data["annual_income"].items():
        rev = metrics.get("Total Revenue")
        if rev is not None:
            fy = date_str[:4]
            result[f"FY{fy}"] = rev * usd_rate

    for date_str, metrics in data["quarterly_income"].items():
        rev = metrics.get("Total Revenue")
        if rev is not None:
            try:
                d = pd.Timestamp(date_str)
                month = d.month
                q = (month - 1) // 3 + 1
                result[f"{d.year}Q{q}"] = rev * usd_rate
            except Exception:
                pass

    return result


def get_business_summary(ticker: str) -> str:
    """Return the long business summary from Yahoo Finance."""
    data = get_financials(ticker)
    return data.get("info", {}).get("longBusinessSummary", "") or ""


def get_key_metrics(ticker: str) -> Dict[str, Optional[float]]:
    """Return key P&L metrics from the most recent annual report."""
    data = get_financials(ticker)
    usd = data.get("usd_rate", 1.0)

    def latest_val(income_dict: Dict, metric: str) -> Optional[float]:
        for date_str in sorted(income_dict.keys(), reverse=True):
            val = income_dict[date_str].get(metric)
            if val is not None:
                return val * usd
        return None

    ann = data["annual_income"]
    return {
        "total_revenue":      latest_val(ann, "Total Revenue"),
        "gross_profit":       latest_val(ann, "Gross Profit"),
        "operating_income":   latest_val(ann, "Operating Income"),
        "net_income":         latest_val(ann, "Net Income"),
        "rd_expense":         latest_val(ann, "Research And Development"),
        "sga_expense":        latest_val(ann, "Selling General And Administration"),
        "interest_expense":   latest_val(ann, "Interest Expense"),
        "income_tax":         latest_val(ann, "Tax Provision"),
        "ebitda":             latest_val(ann, "EBITDA"),
    }
