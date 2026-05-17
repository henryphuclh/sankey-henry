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
FILING_TYPES_INTL = ["20-F", "40-F", "6-K"]

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

# ── Extraction thresholds ─────────────────────────────────────────────────────
XBRL_COVERAGE_MIN      = 0.70          # Below this → fall back to LLM extraction
SEGMENT_MIN_PCT        = 0.005         # Drop segments < 0.5% of total revenue
SEGMENT_MIN_VALUE      = 50_000_000    # Drop segments < $50 M regardless of pct
SEGMENT_RESCALE_MIN    = 0.97          # Rescale down when segments >3% over total_revenue (eliminations)
SEGMENT_RESCALE_MAX    = 1.25
REVENUE_WARN_DIFF      = 0.15          # Warn when SEC vs Yahoo revenue differs > 15%

# ── Financial sector thresholds ───────────────────────────────────────────────
BANK_NII_MIN_PCT         = 0.05   # NII must be > 5% of revenue to classify as bank
BANK_PROVISION_MAX_PCT   = 0.15   # Provision > 15% of revenue → likely gross interest, discard
INSURANCE_COGS_MIN_PCT   = 0.20   # COGS/claims > 20% of revenue → insurance company
INSURANCE_CLAIMS_MAX_PCT = 0.95   # Insurance claims cannot exceed 95% of revenue
INSURANCE_BENEFITS_MIN_PCT = 0.30 # Benefits > 30% of revenue → insurance-dominant

# ── Rate limits ────────────────────────────────────────────────────────────────
YAHOO_RATE_LIMIT = 2   # requests per second for Yahoo Finance

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

# ── XBRL axis overrides ───────────────────────────────────────────────────────
# Tickers whose StatementBusinessSegmentsAxis returns geographic/mixed breakdowns
# but whose ProductOrServiceAxis is more investor-relevant.
# ── CIK seed map ──────────────────────────────────────────────────────────────
# Fallback CIKs for international filers whose ADR tickers are not directly
# resolvable via edgartools.  data_source_checker tries edgartools first and
# falls back to this map.  Classification logic does NOT use this map.
TICKER_TO_CIK: dict = {}  # CIK resolution is fully automatic via data_source_checker

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
