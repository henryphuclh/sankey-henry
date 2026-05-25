"""Yahoo Finance client — income statements, segment data, currency conversion."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.cache.cache_manager import cache
from src.ingestion.rate_limiter import yahoo_limiter

logger = logging.getLogger(__name__)


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
        usd_rates        : {date_str: float} — exchange rate at period end date
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
        "usd_rates":        {},  # {date_str: float} — rate at period end date
    }

    try:
        ann = tkr.income_stmt
        if ann is not None and not ann.empty:
            result["annual_income"] = _df_to_dict(ann)
        else:
            logger.warning("get_financials(%s): annual income_stmt empty or None", ticker)
    except Exception as e:
        logger.warning("get_financials(%s): failed to fetch annual income_stmt: %s", ticker, e)

    try:
        qtr = tkr.quarterly_income_stmt
        if qtr is not None and not qtr.empty:
            result["quarterly_income"] = _df_to_dict(qtr)
        else:
            logger.warning("get_financials(%s): quarterly income_stmt empty or None", ticker)
    except Exception as e:
        logger.warning("get_financials(%s): failed to fetch quarterly income_stmt: %s", ticker, e)

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

    # Per-period exchange rates at period end date
    if currency != "USD":
        all_dates = list(result["annual_income"]) + list(result["quarterly_income"])
        usd_rates = {}
        for d in all_dates:
            rate = _get_usd_rate(currency, as_of_date=d)
            if rate is not None:
                usd_rates[d] = rate
        result["usd_rates"] = usd_rates

    cache.set("yfinance", ticker, result)
    return result


def _get_usd_rate(currency: str, as_of_date: Optional[str] = None) -> float:
    """Return exchange rate: 1 unit of `currency` in USD, at `as_of_date` (ISO date) or latest."""
    if currency == "USD":
        return 1.0
    pair = f"{currency}USD=X"
    cache_key = f"fx_{pair}_{as_of_date}" if as_of_date else f"fx_{pair}"
    cached = cache.get("yfinance", cache_key)
    if cached:
        return cached.get("rate", 1.0)

    def _extract_close(df) -> float:
        if df is None or df.empty:
            return 0.0
        close = df["Close"]
        if hasattr(close, "values"):
            v = close.values[-1]
            if hasattr(v, "__len__") and len(v) == 1:
                v = v[0]
            return float(v)
        return 0.0

    if as_of_date:
        d = date.fromisoformat(as_of_date[:10])
        kwargs = {"start": str(d - timedelta(days=7)), "end": str(d + timedelta(days=2))}
    else:
        kwargs = {"period": "5d"}

    pair2 = f"USD{currency}=X"
    try:
        yahoo_limiter()
        rate = _extract_close(yf.download(pair, auto_adjust=True, progress=False, **kwargs))
        if rate > 0:
            cache.set("yfinance", cache_key, {"rate": rate})
            return rate
    except Exception:
        pass
    try:
        yahoo_limiter()
        rate2 = _extract_close(yf.download(pair2, auto_adjust=True, progress=False, **kwargs))
        if rate2 > 0:
            rate = 1.0 / rate2
            cache.set("yfinance", cache_key, {"rate": rate})
            return rate
    except Exception:
        pass
    logger.warning("_get_usd_rate: could not fetch %s rate for %s — period will be kept in original currency", currency, as_of_date or "latest")
    return None


def get_total_revenue_by_period(ticker: str) -> Dict[str, Optional[float]]:
    """
    Return {period_str: total_revenue_usd} for all available annual and quarterly periods.
    Period string format: 'FY2024' or '2024Q3'.
    """
    data = get_financials(ticker)
    usd_rates = data.get("usd_rates", {})
    result: Dict[str, Optional[float]] = {}

    for date_str, metrics in data["annual_income"].items():
        rev = metrics.get("Total Revenue")
        if rev is not None:
            fy = date_str[:4]
            result[f"FY{fy}"] = rev * usd_rates.get(date_str, 1.0)

    for date_str, metrics in data["quarterly_income"].items():
        rev = metrics.get("Total Revenue")
        if rev is not None:
            try:
                d = pd.Timestamp(date_str)
                q = (d.month - 1) // 3 + 1
                result[f"{d.year}Q{q}"] = rev * usd_rates.get(date_str, 1.0)
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
    usd_rates = data.get("usd_rates", {})

    def latest_val(income_dict: Dict, metric: str) -> Optional[float]:
        for date_str in sorted(income_dict.keys(), reverse=True):
            val = income_dict[date_str].get(metric)
            if val is not None:
                return val * usd_rates.get(date_str, 1.0)
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
