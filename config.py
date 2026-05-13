from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(override=True)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
CACHE_DIR  = DATA_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output" / "sankey"
EXCEL_PATH = BASE_DIR / "Valuation_Top100_2026-04-18.xlsx"

# ── API Keys ──────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Henry.Phuclh@gmail.com")

# ── SEC EDGAR ─────────────────────────────────────────────────────────────────
SEC_BASE_URL       = "https://data.sec.gov"
SEC_RATE_LIMIT     = 8
SEC_RETRY_MAX      = 5
SEC_RETRY_BASE_SEC = 2.0

FILING_TYPES_US   = ["10-K", "10-Q"]
FILING_TYPES_INTL = ["20-F", "6-K"]

# ── Coverage ──────────────────────────────────────────────────────────────────
YEARS_BACK    = 3
QUARTERS_BACK = 12

# ── LLM Models (OpenAI only) ──────────────────────────────────────────────────
OPENAI_MODEL_EXTRACTION = os.getenv("OPENAI_MODEL_EXTRACTION", "gpt-4o-mini")
OPENAI_MODEL_FALLBACK   = os.getenv("OPENAI_MODEL_FALLBACK",   "gpt-4o")
OPENAI_MODEL_SIMPLE     = os.getenv("OPENAI_MODEL_SIMPLE",     "gpt-4o-mini")

LLM_MAX_TOKENS        = 2048
FILING_TEXT_MAX_CHARS = 120000
SMART_SLICE_PRE       = 30000
SMART_SLICE_POST      = 90000

# ── XBRL ──────────────────────────────────────────────────────────────────────
XBRL_CONFIDENCE_HIGH   = 0.75
XBRL_CONFIDENCE_MEDIUM = 0.30

XBRL_REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SegmentReportingInformationRevenue",
]
XBRL_SEGMENT_AXIS = "StatementBusinessSegmentsAxis"

# ── Cache TTL (days) ──────────────────────────────────────────────────────────
CACHE_TTL = {
    "filings":  30,
    "xbrl":      7,
    "yfinance":  7,
    "llm":      90,
    "segments": 30,
    "checker":  30,   # data_source_checker results
}

# ── Concurrency ───────────────────────────────────────────────────────────────
MAX_TICKER_WORKERS = 3
MAX_LLM_WORKERS    = 20

# ── CIK seed map ──────────────────────────────────────────────────────────────
# Fallback CIKs for international filers whose ADR tickers are not directly
# resolvable via edgartools.  data_source_checker tries edgartools first and
# falls back to this map.  Classification logic does NOT use this map.
TICKER_TO_CIK = {
    "TSM":   "0001046179",
    "ASML":  "0000937966",
    "TCEHY": "0001495479",
    "RHHBY": "0001114388",
    "HSBC":  "0000083246",
    "AZN":   "0000901832",
    "NVS":   "0001114448",
}

# ── Sankey Visual Config ──────────────────────────────────────────────────────
SANKEY_COLORS = {
    "segment":          "#1f77b4",
    "total_revenue":    "#2ca02c",
    "gross_profit":     "#2ca02c",
    "cogs":             "#d62728",
    "rd":               "#ff7f0e",
    "sga":              "#ff7f0e",
    "other_opex":       "#ffbb78",
    "operating_income": "#2ca02c",
    "interest":         "#7f7f7f",
    "tax":              "#7f7f7f",
    "net_income":       "#2ca02c",
    "net_loss":         "#d62728",
}
SANKEY_MIN_LINK_PCT = 0.01

# ── GICS Sector mapping (display only — not used for classification) ───────────
TICKER_SECTOR: dict = {
    "NVDA": "Information Technology",
    "AAPL": "Information Technology",
    "MSFT": "Information Technology",
    "AMZN": "Consumer Discretionary",
    "GOOGL": "Communication Services",
    "TSM":  "Information Technology",
    "AVGO": "Information Technology",
    "GOOG": "Communication Services",
    "TSLA": "Consumer Discretionary",
    "META": "Communication Services",
    "BRK-B": "Financials",
    "WMT":  "Consumer Staples",
    "005930.KS": "Information Technology",
    "LLY":  "Health Care",
    "JPM":  "Financials",
    "XOM":  "Energy",
    "TCEHY": "Communication Services",
    "ASML": "Information Technology",
    "JNJ":  "Health Care",
    "000660.KS": "Information Technology",
    "V":    "Financials",
    "MU":   "Information Technology",
    "ORCL": "Information Technology",
    "MA":   "Financials",
    "AMD":  "Information Technology",
    "COST": "Consumer Staples",
    "NFLX": "Communication Services",
    "BAC":  "Financials",
    "CAT":  "Industrials",
    "ABBV": "Health Care",
    "CVX":  "Energy",
    "HD":   "Consumer Discretionary",
    "PG":   "Consumer Staples",
    "CSCO": "Information Technology",
    "INTC": "Information Technology",
    "9988.HK": "Consumer Discretionary",
    "PLTR": "Information Technology",
    "LRCX": "Information Technology",
    "RHHBY": "Health Care",
    "KO":   "Consumer Staples",
    "GE":   "Industrials",
    "HSBC": "Financials",
    "AZN":  "Health Care",
    "AMAT": "Information Technology",
    "MS":   "Financials",
    "UNH":  "Health Care",
    "NVS":  "Health Care",
    "MRK":  "Health Care",
    "MC.PA": "Consumer Discretionary",
    "TM":   "Consumer Discretionary",
    "GS":   "Financials",
    "GEV":  "Industrials",
    "RTX":  "Industrials",
    "NESN.SW": "Consumer Staples",
    "WFC":  "Financials",
    "RY.TO": "Financials",
    "SHEL.L": "Energy",
    "PM":   "Consumer Staples",
    "IBM":  "Information Technology",
    "KLAC": "Information Technology",
    "C":    "Financials",
    "AXP":  "Financials",
    "LIN":  "Materials",
    "SIE.DE": "Industrials",
    "MCD":  "Consumer Discretionary",
    "CBA.AX": "Financials",
    "PEP":  "Consumer Staples",
    "SAP.DE": "Information Technology",
    "8306.T": "Financials",
    "TXN":  "Information Technology",
    "TMO":  "Health Care",
    "VZ":   "Communication Services",
    "AMGN": "Health Care",
    "NEE":  "Utilities",
    "DIS":  "Communication Services",
    "SAN.MC": "Financials",
    "APH":  "Information Technology",
    "T":    "Communication Services",
    "NOVO-B.CO": "Health Care",
    "TJX":  "Consumer Discretionary",
    "BA":   "Industrials",
    "TD.TO": "Financials",
    "ALV.DE": "Financials",
    "BLK":  "Financials",
    "SHOP.TO": "Information Technology",
    "ABT":  "Health Care",
    "CRM":  "Information Technology",
    "ISRG": "Health Care",
    "APP":  "Information Technology",
    "SCHW": "Financials",
    "UBER": "Consumer Discretionary",
    "QCOM": "Information Technology",
    "SPGI": "Financials",
    "6758.T": "Consumer Discretionary",
    "ACN":  "Information Technology",
    "INTU": "Information Technology",
    "NOW":  "Information Technology",
    "BKNG": "Consumer Discretionary",
}