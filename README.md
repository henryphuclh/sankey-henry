# Financial Analysis System

Automated financial analysis pipeline for 98 global equities. The system fetches data from SEC EDGAR and Yahoo Finance, extracts business segments using XBRL and LLM, computes P&L metrics, and generates interactive HTML reports with Sankey flow charts and Income Statement tables with YoY comparisons.

**Live output:** `output/sankey/index.html` — sortable dashboard of all 98 companies.

---

## Table of Contents

1. [Setup](#setup)
2. [Running the pipeline](#running-the-pipeline)
3. [Project structure](#project-structure)
4. [How it works](#how-it-works)
   - [Ticker classification](#step-1--ticker-classification)
   - [Filing ingestion](#step-2--filing-ingestion)
   - [P&L extraction](#step-3--pl-extraction-by-sector)
   - [Segment extraction](#step-4--segment-extraction)
   - [Segment normalization](#step-5--segment-normalization)
   - [Multi-period aggregation](#step-6--multi-period-aggregation)
5. [Sankey chart construction](#sankey-chart-construction)
6. [Income Statement table & YoY](#income-statement-table--yoy)
7. [Cache system](#cache-system)
8. [Configuration reference](#configuration-reference)
9. [Operating notes](#operating-notes)

---

## Setup

Requires Python 3.8+.

```bash
pip install -r requirements.txt
```

Create `.env` in the project root:

```
OPENAI_API_KEY=sk-YOUR_KEY_HERE
SEC_USER_AGENT=YourName email@example.com
```

- `OPENAI_API_KEY`: from https://platform.openai.com/api-keys. Full 98-ticker run costs roughly $1–3 using `gpt-4o-mini`.
- `SEC_USER_AGENT`: required by SEC EDGAR policy. Use a real name and email to avoid IP throttling.

---

## Running the pipeline

```bash
# Process specific tickers
python main.py --tickers NVDA AAPL MSFT AMZN

# Process all 98 tickers
python main.py --all
```

| Flag | Description |
|------|-------------|
| `--no-cache` | Ignore all cached data, re-fetch everything from source |
| `--verbose` / `-v` | Print per-ticker processing details |
| `--workers N` | Number of tickers processed in parallel (default: 3) |
| `--output-dir PATH` | Override HTML output directory |

After the run, open `output/sankey/index.html` in a browser to see the full dashboard.

**Re-runs without `--no-cache`** skip all API calls and only re-render HTML from the aggregated JSON files in `data/segments/`. This is very fast (seconds).

---

## Project structure

```
.
├── main.py                          # CLI entry point, orchestrates the pipeline
├── config.py                        # All global constants and thresholds
├── requirements.txt
├── Valuation_Top100_2026-04-18.xlsx # 98-ticker input list (preserves display order)
│
├── src/
│   ├── ingestion/
│   │   ├── ticker_loader.py         # Reads Excel; auto-classifies US_SEC / INTL_SEC / INTL_YAHOO
│   │   ├── data_source_checker.py   # Resolves CIK, inspects recent SEC filings, assigns sector
│   │   ├── edgar_client.py          # SEC EDGAR fetching, XBRL parsing, segment axis logic
│   │   ├── yahoo_client.py          # Yahoo Finance financials and revenue validation
│   │   ├── filing_router.py         # Routes each ticker to the correct data sources
│   │   └── rate_limiter.py          # Token-bucket rate limiter (SEC: 8 req/s)
│   │
│   ├── extraction/
│   │   ├── models.py                # Dataclasses: FilingRecord, SegmentData, SegmentValue
│   │   ├── extraction_router.py     # Orchestrates XBRL → LLM fallback per filing
│   │   ├── llm_extractor.py         # OpenAI-based segment extraction from filing text
│   │   ├── normalizer.py            # Fuzzy-matches segment names across periods
│   │   └── sector_handlers/
│   │       ├── financials.py        # P&L for banks/insurers (NII, non-interest income)
│   │       ├── pharma.py            # P&L for pharma/biotech
│   │       └── standard.py          # P&L for all other companies
│   │
│   ├── analysis/
│   │   ├── segment_aggregator.py    # Merges multi-period data, computes TTM, segment trends
│   │   └── business_model_writer.py # LLM narrative for each company's business model
│   │
│   ├── visualization/
│   │   ├── sankey_builder.py        # Builds 6-layer P&L flow nodes and links
│   │   ├── sankey_renderer.py       # Renders Sankey via Plotly, period dropdown
│   │   ├── report_generator.py      # Assembles full HTML (Sankey + IS table + coverage)
│   │   └── index_generator.py       # Builds sortable dashboard index.html
│   │
│   ├── cache/
│   │   └── cache_manager.py         # File-based JSON cache with per-namespace TTL
│   └── llm/
│       └── provider.py              # OpenAI wrapper with retry and rate limiting
│
├── data/
│   ├── cache/
│   │   ├── checker/                 # data_source_checker results (TTL 30 days)
│   │   ├── filings/                 # SEC filing lists (TTL 30 days)
│   │   ├── llm/                     # LLM extraction results (TTL 90 days)
│   │   └── yfinance/                # Yahoo Finance data (TTL 7 days)
│   └── segments/                    # Final aggregated JSON per ticker
│
└── output/
    └── sankey/
        ├── index.html               # Dashboard — all 98 companies
        └── {TICKER}_sankey.html     # Per-company interactive report
```

---

## How it works

### Step 1 — Ticker classification

`data_source_checker.py` resolves each ticker's CIK via edgartools and inspects the last 3 years of SEC submissions to classify it:

| Class | Count | Source | Filing types |
|-------|-------|--------|--------------|
| **US_SEC** | 74 | SEC EDGAR | 10-K (annual), 10-Q (quarterly) |
| **INTL_SEC** | 15 | SEC EDGAR | 20-F or **40-F** (annual), 6-K (quarterly) |
| **INTL_YAHOO** | 9 | Yahoo Finance only | — |

**40-F** is the Canadian annual report format filed with the SEC (used by RY.TO and TD.TO instead of 20-F). The system automatically detects it from the submissions feed and treats it as equivalent to 20-F for all purposes.

INTL_YAHOO tickers (no SEC filings): 005930.KS (Samsung), 000660.KS (SK Hynix), TCEHY, RHHBY, MC.PA, NESN.SW, SIE.DE, CBA.AX, ALV.DE.

Each ticker is also assigned a **sector** based on SIC code:

| Sector | Count | Handler |
|--------|-------|---------|
| **standard** | 65 | Generic US-GAAP P&L |
| **financial** | 20 | Banks / insurers (NII + non-interest income) |
| **pharma** | 13 | Pharmaceutical / biotech |

### Step 2 — Filing ingestion

For US_SEC and INTL_SEC tickers, `edgar_client.py` fetches the filing list from:

```
GET https://data.sec.gov/submissions/{CIK}.json
```

Each filing becomes a `FilingRecord` (ticker, form type, period, accession number, CIK). SEC rate limit: 8 req/s with exponential-backoff retry (up to 5 attempts, base delay 2 s). Filing lists are cached 30 days.

For INTL_YAHOO tickers, only Yahoo Finance data is used — no SEC filings are fetched.

### Step 3 — P&L extraction by sector

`edgartools` parses the XBRL income statement from each filing directly. The sector handler maps XBRL concepts to P&L fields:

**Standard / Pharma** — US-GAAP concepts:

| Field | XBRL concept |
|-------|-------------|
| `total_revenue` | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` |
| `gross_profit` | `GrossProfit` |
| `operating_income` | `OperatingIncomeLoss` |
| `net_income` | `NetIncomeLoss` |
| `cogs` | `CostOfRevenue`, `CostOfGoodsSold` |
| `rd_expense` | `ResearchAndDevelopmentExpense` |
| `sga_expense` | `SellingGeneralAndAdministrativeExpense` |
| `interest_expense` | `InterestExpense` |
| `income_tax` | `IncomeTaxExpense` |

**Financial sector** — uses bank/insurer-specific concepts:

| Field | Concepts tried |
|-------|----------------|
| `total_revenue` | `Revenues`, `RevenueFromContractWithCustomer`, `NetInterestIncome`, `InterestAndFeeIncome`, `ifrs-full:Revenue` |
| `net_income` | `NetIncomeLoss`, `ProfitLoss` (IFRS) |

If the financial handler returns no revenue (common for IFRS banks where the P&L only captures NII), the system falls back to standard extraction.

### Step 4 — Segment extraction

This is the most complex step. The system attempts XBRL extraction first, then falls back to LLM if needed.

#### 4a — XBRL dimension extraction

`segments_from_xbrl_dimensions()` searches the income statement DataFrame for dimensioned revenue rows. Axes are tried in priority order:

```
1. us-gaap:StatementBusinessSegmentsAxis     # ASC 280 reportable segments
2. ifrs-full:SegmentsAxis                    # IFRS IAS 8 business segments (TD.TO, RY.TO, NOVO-B.CO)
3. srt:ConsolidationItemsAxis
4. ifrs-full:SegmentConsolidationItemsAxis   # IFRS consolidation axis (RY.TO)
5. srt:ProductOrServiceAxis
6. ifrs-full:ProductsAndServicesAxis
7. srt:StatementGeographicalAxis
8. ifrs-full:GeographicalAreasAxis
```

Several axes are **blocked** entirely (financial instrument categories, carrying amount axes, employee benefit axes, etc.) to prevent misidentifying non-segment data as business segments.

**Revenue concept filtering** — only concepts matching these hints are considered:
- Standard/pharma: `Revenue`, `Sales`, `NetSales`
- Financial sector: `Revenue`, `Sales`, `NetSales`, `Noninterest`, `Premium`, `Fee`, `Interest`

**Subtotal-drop heuristic** — after collecting (name, value) pairs, the largest member is iteratively dropped until the sum falls within ±15% of `total_revenue`. This removes "Total" or "Operating Segments" rollup rows that appear alongside leaf segments.

#### 4b — `dimension_label` fallback

Some filers (MRK, RY.TO) encode segment membership in the `dimension_label` column rather than as the primary `dimension_axis`. For example, MRK uses `ConsolidationItemsAxis` as the primary axis but stores `StatementBusinessSegmentsAxis: Pharmaceutical` in `dimension_label`.

When no usable axis is found via 4a, the system scans `dimension_label` for rows containing either `StatementBusinessSegmentsAxis` or `ifrs-full:SegmentsAxis`, excludes rows that also contain product/geo/financial-instrument axes, and extracts the segment name from the label string.

**IFRS bank partial-revenue guard** — IFRS banks (RY.TO, TD.TO) often report only net interest income in `total_revenue` from the P&L handler (~half of actual segment revenue). When the sum of extracted segments exceeds `1.5 × total_revenue`, the reference is treated as partial and the subtotal-drop heuristic is disabled.

#### 4c — LLM fallback

If XBRL coverage (`sum of segment values / total_revenue`) is below 0.7, the system extracts the Segment Information note from the filing text and sends it to OpenAI:

```python
note_text = get_segment_note_text(obj)   # typically 5,000–30,000 chars
```

Smart slicing: 30,000 chars before the "Segment" anchor + 90,000 chars after, capped at 120,000 total (~30k tokens). Primary model: `gpt-4o-mini`; auto-fallback to `gpt-4o` on failure. LLM results are cached 90 days.

The final method label reflects what was used:
- `xbrl` — XBRL coverage ≥ 70%
- `edgar+llm` — LLM beat XBRL coverage
- `yahoo+llm` — INTL_YAHOO ticker, segments from Yahoo financial text

#### 4d — Yahoo Finance cross-validation

When Yahoo revenue data is available, a warning is appended if SEC vs. Yahoo revenue differs by more than 15%.

### Step 5 — Segment normalization

`normalizer.py` unifies segment names that change wording across periods (e.g. "Greater China" → "China" → "Mainland China, Hong Kong and Taiwan"). It uses fuzzy matching with `rapidfuzz`:

```python
best_match, score, _ = process.extractOne(
    name, canonical_names, scorer=fuzz.token_sort_ratio
)
if score >= 80:
    canonical_map[name] = best_match   # merge into existing canonical name
else:
    canonical_names.append(name)       # new distinct segment
```

`token_sort_ratio` sorts tokens before comparing, so "Cloud Computing Services" and "Services Cloud Computing" score 100.

Canonical names are chosen by descending length (longer = more descriptive). Per-ticker canonical maps are saved to `data/segments/{TICKER}_canonical.json` for manual inspection.

### Step 6 — Multi-period aggregation

`segment_aggregator.py` produces a single JSON per ticker:

```python
{
  "latest_annual":      <most recent annual SegmentData>,
  "annual_periods":     [<FY2025>, <FY2024>, <FY2023>],        # up to 3
  "quarterly_periods":  [<2026Q1>, <2025Q4>, ..., <2023Q2>],   # up to 12
  "segment_trend":      {"iPhone": [{"period": "FY2023", "value": ...}, ...]},
  "ttm_revenue":        <sum of 4 most recent quarters>,
  "annual_count":       3,
  "quarterly_count":    12
}
```

TTM is computed from the 4 most recent quarterly records; if fewer than 4 are available it falls back to the most recent annual value.

---

## Sankey chart construction

The Sankey represents the P&L flow across 6 layers:

```
Layer 0: Business segments ─┐
                             ├─► Layer 1: Total Revenue ─► Layer 2a: Gross Profit ─► Layer 3: R&D / SG&A / Other OpEx
                                                         │                                      └─► Layer 4: Operating Income ─► Layer 5: Tax / Interest
                                                         └─► Layer 2b: COGS (exits)                                              └─► Layer 6: Net Income
```

**Missing value inference:**
- If `gross_profit` is known but `cogs` is not: `cogs = total_revenue − gross_profit`
- If `cogs` is known but `gross_profit` is not: `gross_profit = total_revenue − cogs`
- If both are absent: estimated from `operating_income + rd_expense + sga_expense`

**Small-segment bucketing:** segments below 1.5% of revenue (or beyond the top 8) are merged into an "Other" node.

**Minimum link width:** links are floored at 1% of total revenue so no flow disappears visually.

**Node x-positions:**

| Node | x |
|------|---|
| Segments | 0.00 |
| Total Revenue | 0.20 |
| Gross Profit | 0.40 |
| R&D / SG&A / Other OpEx | 0.60 |
| Operating Income | 0.75 |
| Tax / Interest | 0.87 |
| Net Income | 1.00 |

**Colors** (from `config.py`):

| Node type | Color |
|-----------|-------|
| Segment | `#1f77b4` (blue) |
| Revenue / profit nodes | `#2ca02c` (green) |
| Cost / loss nodes | `#d62728` (red) |
| R&D, SG&A | `#ff7f0e` (orange) |
| Tax, interest | `#7f7f7f` (grey) |

Links use a semi-transparent version (alpha 0.4) of the source node color.

**Period dropdown:** each fiscal period is pre-built as a separate `SankeyData` object. All period data is embedded as JSON in the HTML `<script>` block. Switching periods calls `Plotly.react()` — no page reload.

---

## Income Statement table & YoY

### Annual mode

Compares the two most recent annual periods side by side:

| Metric | FY2025 | FY2024 | YoY% |
|--------|--------|--------|------|

### Quarterly mode

Each recent quarter is paired with the same quarter of the prior year (e.g. 2025Q4 vs 2024Q4). Up to 4 pairs are shown as tabs.

YoY formula:

```python
yoy_pct = (current - prior) / abs(prior) * 100
```

`abs(prior)` handles the case where the prior year was a loss.

**Confidence score** (0.0 – 1.0) reflects data completeness:

| Criterion | Points |
|-----------|--------|
| `total_revenue` present | +0.40 |
| `gross_profit` present | +0.10 |
| `operating_income` present | +0.15 |
| `net_income` present | +0.15 |
| Each detail line (cogs, rd, sga, interest, tax) | +0.04 each, max 0.20 |
| ≥ 2 segments | +0.30 |
| 1 segment | +0.10 |
| 0 segments | cap at 0.55 |

---

## Cache system

Cache files live in `data/cache/` as JSON with a Unix timestamp:

```json
{ "timestamp": 1714320000.0, "data": { ... } }
```

TTL by namespace:

| Namespace | TTL | Rationale |
|-----------|-----|-----------|
| `filings` | 30 days | Filing lists change rarely |
| `checker` | 30 days | Classification is stable |
| `yfinance` | 7 days | Prices update weekly |
| `xbrl` | 7 days | Minor XBRL amendments possible |
| `llm` | 90 days | Expensive to re-run, filing text stable |
| `segments` | 30 days | Final aggregated output |

File names are derived from the namespace + key with `/`, `\`, `:` replaced by `_`. LLM cache keys include an MD5 of the prompt so that prompt changes automatically invalidate the cache.

---

## Configuration reference

Key constants in `config.py`:

```python
# Data coverage
YEARS_BACK    = 3   # annual filings to fetch (also controls _get_recent_forms in checker)
QUARTERS_BACK = 12  # quarterly filings to fetch

# SEC EDGAR rate limiting
SEC_RATE_LIMIT     = 8    # requests per second
SEC_RETRY_MAX      = 5    # max retry attempts
SEC_RETRY_BASE_SEC = 2.0  # base delay for exponential backoff

# Filing types
FILING_TYPES_US   = ["10-K", "10-Q"]
FILING_TYPES_INTL = ["20-F", "40-F", "6-K"]   # 40-F for Canadian filers

# Concurrency
MAX_TICKER_WORKERS = 3    # parallel tickers (keep low to respect SEC rate limit)
MAX_LLM_WORKERS    = 20   # parallel LLM calls within one ticker

# XBRL thresholds
XBRL_COVERAGE_MIN      = 0.70   # below this → LLM fallback
XBRL_CONFIDENCE_HIGH   = 0.75
XBRL_CONFIDENCE_MEDIUM = 0.30

# Segment filtering
SEGMENT_MIN_PCT        = 0.005  # drop segments < 0.5% of revenue
SEGMENT_MIN_VALUE      = 50_000_000  # drop segments < $50M
SEGMENT_RESCALE_MIN    = 0.80   # rescale only when scale ∉ [0.80, 1.25]
SEGMENT_RESCALE_MAX    = 1.25
REVENUE_WARN_DIFF      = 0.15   # warn when SEC vs Yahoo revenue differs > 15%

# Financial sector thresholds
BANK_NII_MIN_PCT         = 0.05   # NII > 5% → classify as bank
BANK_PROVISION_MAX_PCT   = 0.15   # provision > 15% → likely gross interest, discard
INSURANCE_COGS_MIN_PCT   = 0.20   # COGS/claims > 20% → insurance
INSURANCE_CLAIMS_MAX_PCT = 0.95   # insurance claims cap

# LLM
OPENAI_MODEL_EXTRACTION = "gpt-4o-mini"
OPENAI_MODEL_FALLBACK   = "gpt-4o"
FILING_TEXT_MAX_CHARS   = 120_000  # ~30k tokens
SMART_SLICE_PRE         = 30_000
SMART_SLICE_POST        = 90_000
```

---

## Operating notes

- **Do not raise `MAX_TICKER_WORKERS` above 5.** SEC EDGAR enforces a global 10 req/s cap across all threads. Exceeding it results in HTTP 429 and a temporary IP ban.
- **First full run** (`--all`) takes 20–40 minutes depending on network speed and how many filings require LLM extraction. Subsequent runs with cache take a few seconds.
- **INTL_YAHOO tickers** (9 companies: Samsung, SK Hynix, Tencent, Roche, LVMH, Nestlé, Siemens, CBA, Allianz) have no segment breakdown available — they don't file with the SEC and Yahoo Finance doesn't provide granular segment data. Confidence scores are typically below 0.60.
- **IFRS banks** (RY.TO, TD.TO, SAN.MC, HSBC, 8306.T) report NII-only in the main P&L; full segment revenue (NII + non-interest income) comes from the XBRL segment note. The system handles this via the 1.5× partial-revenue guard.
- **40-F filers** (RY.TO, TD.TO): annual reports are 40-F, quarterly reports are 6-K. The system automatically identifies and processes both.
- To force-reprocess a single ticker: `python main.py --tickers RY.TO --no-cache`
- To rebuild HTML only (no API calls): `python main.py --all` (without `--no-cache`)
