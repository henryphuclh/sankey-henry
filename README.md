# Sankey Financial Analysis — Task 1

Automated pipeline that fetches financial data for 98 global stocks, extracts business segment breakdowns, and generates interactive HTML reports with Sankey flow charts, income statement tables, and LLM-written business model summaries.

**Open the results:** `output/sankey/index.html`

---

## Table of Contents

1. [Setup](#setup)
2. [How to run](#how-to-run)
3. [Project structure](#project-structure)
4. [How the pipeline works](#how-the-pipeline-works)
5. [Sankey chart](#sankey-chart)
6. [Income statement table](#income-statement-table)
7. [Cache system](#cache-system)
8. [Configuration](#configuration)
9. [Known limitations & data coverage notes](#known-limitations--data-coverage-notes)
10. [Q&A — Presentation preparation](#qa--presentation-preparation)

---

## Setup

Requires Python 3.8+.

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-YOUR_KEY_HERE
SEC_USER_AGENT=YourName email@example.com
```

- `OPENAI_API_KEY` — from https://platform.openai.com/api-keys. A full run of all 98 tickers costs roughly $1–3 using `gpt-4o-mini`.
- `SEC_USER_AGENT` — required by SEC EDGAR. Use a real name and email to avoid being rate-limited.

---

## How to run

```bash
# Run for specific tickers
python main.py --tickers NVDA AAPL MSFT AMZN

# Run for all 98 tickers
python main.py --all
```

| Flag | What it does |
|------|-------------|
| `--no-cache` | Ignore all saved data and re-fetch everything from source |
| `--verbose` / `-v` | Print details for each ticker as it runs |
| `--workers N` | How many tickers to process at the same time (default: 3) |
| `--output-dir PATH` | Change the folder where HTML files are saved |

After running, open `output/sankey/index.html` in a browser to browse all companies.

**Re-running without `--no-cache`** skips all API calls and only regenerates HTML from the saved JSON files. This takes a few seconds.

---

## Project structure

```
.
├── main.py                          # Entry point — runs the full pipeline
├── config.py                        # All global settings and thresholds
├── requirements.txt
├── Valuation_Top100_2026-04-18.xlsx # The 98-ticker input list
│
├── src/
│   ├── ingestion/
│   │   ├── ticker_loader.py         # Reads the Excel file, classifies each ticker
│   │   ├── data_source_checker.py   # Looks up each company's CIK and filing history
│   │   ├── edgar_client.py          # Downloads and parses SEC filings (XBRL)
│   │   ├── yahoo_client.py          # Downloads financial data from Yahoo Finance
│   │   ├── filing_router.py         # Decides which data source to use for each ticker
│   │   └── rate_limiter.py          # Limits SEC requests to 8/second
│   │
│   ├── extraction/
│   │   ├── models.py                # Data structures: FilingRecord, SegmentData
│   │   ├── extraction_router.py     # Runs XBRL extraction, falls back to LLM if needed
│   │   ├── llm_extractor.py         # Sends filing text to OpenAI and parses segments
│   │   ├── normalizer.py            # Matches segment names across different periods
│   │   └── sector_handlers/
│   │       ├── financials.py        # P&L logic for banks and insurance companies
│   │       ├── pharma.py            # P&L logic for pharmaceutical companies
│   │       └── standard.py          # P&L logic for all other companies
│   │
│   ├── analysis/
│   │   ├── segment_aggregator.py    # Combines data from all periods into one summary
│   │   ├── business_model_writer.py # Uses LLM to write a business model analysis
│   │   └── coverage_explainer.py   # Uses LLM to explain why data is missing (if any)
│   │
│   ├── visualization/
│   │   ├── sankey_builder.py        # Builds the node and link structure for the Sankey
│   │   ├── sankey_renderer.py       # Renders the Sankey chart using Plotly
│   │   ├── report_generator.py      # Assembles the full HTML report per company
│   │   └── index_generator.py       # Builds the index.html dashboard
│   │
│   ├── cache/
│   │   └── cache_manager.py         # Saves and loads JSON cache files with expiry
│   └── llm/
│       └── provider.py              # OpenAI API wrapper with retry logic
│
├── data/
│   ├── cache/
│   │   ├── checker/                 # Saved ticker classification results (30-day TTL)
│   │   ├── filings/                 # Saved SEC filing lists (30-day TTL)
│   │   ├── llm/                     # Saved LLM outputs (90-day TTL)
│   │   └── yfinance/                # Saved Yahoo Finance data (7-day TTL)
│   └── segments/                    # Final aggregated JSON per ticker
│
└── output/
    └── sankey/
        ├── index.html               # Dashboard — browse all 98 companies
        └── {TICKER}_sankey.html     # Per-company report
```

---

## How the pipeline works

### Step 1 — Classify each ticker

The system first checks SEC EDGAR to find out what type of filings each company submits, then assigns it to one of three categories:

| Category | Count | Data source | Filing types used |
|----------|-------|-------------|-------------------|
| **US_SEC** | 74 | SEC EDGAR | 10-K (annual), 10-Q (quarterly) |
| **INTL_SEC** | 15 | SEC EDGAR | 20-F or 40-F (annual), 6-K (quarterly) |
| **INTL_YAHOO** | 9 | Yahoo Finance only | — |

- **40-F** is the Canadian equivalent of 20-F, used by RY.TO and TD.TO.
- **INTL_YAHOO** companies (Samsung, SK Hynix, Tencent, Roche, LVMH, Nestlé, Siemens, CBA, Allianz) do not file with the SEC at all, so all their data comes from Yahoo Finance.

Each company is also assigned a **sector** based on its SIC code:

| Sector | Count | Notes |
|--------|-------|-------|
| **standard** | 65 | Regular companies |
| **financial** | 20 | Banks and insurance companies — different P&L structure |
| **pharma** | 13 | Pharmaceutical and biotech |

### Step 2 — Download filings

For US_SEC and INTL_SEC companies, the system fetches the list of filings from SEC EDGAR. Each filing is stored as a `FilingRecord` object (ticker, type, period, accession number). Filing lists are cached for 30 days.

For INTL_YAHOO companies, only Yahoo Finance is used.

### Step 3 — Extract P&L (revenue, profit, costs)

The system reads the income statement from each EDGAR filing using **XBRL** — a structured data format that companies are required to submit alongside their reports.

It maps XBRL fields to a standard set of P&L metrics:

| Metric | XBRL concept |
|--------|-------------|
| Total revenue | `Revenues`, `RevenueFromContractWithCustomer` |
| Gross profit | `GrossProfit` |
| Operating income | `OperatingIncomeLoss` |
| Net income | `NetIncomeLoss` |
| Cost of revenue | `CostOfRevenue`, `CostOfGoodsSold` |
| R&D expense | `ResearchAndDevelopmentExpense` |
| SG&A expense | `SellingGeneralAndAdministrativeExpense` |

For banks and insurers, different concepts are used (net interest income, non-interest income, etc.).

### Step 4 — Extract business segments

This is the most important step. The system tries three methods in order:

**Method 1 — XBRL dimensions (preferred)**

Business segments in EDGAR filings are stored along a "dimension axis." The system searches for these axes in order:

1. `us-gaap:StatementBusinessSegmentsAxis` — standard US segments (ASC 280)
2. `ifrs-full:SegmentsAxis` — IFRS segments
3. `srt:ProductOrServiceAxis` — product lines
4. `srt:StatementGeographicalAxis` — geographic segments

After collecting segment values, the system applies a **subtotal-drop heuristic**: it removes the largest segment iteratively until the sum is within ±15% of total revenue. This removes "Total" rollup rows that sometimes appear alongside individual segments.

**Method 2 — `dimension_label` scan**

Some companies (e.g. MRK, RY.TO) store segment names inside a text field called `dimension_label` rather than in the axis. When Method 1 finds nothing, the system scans this field for segment references.

**Method 3 — LLM extraction (fallback)**

If XBRL coverage is below 70% of total revenue, the system extracts the "Segment Information" note from the filing text and sends it to OpenAI:

- Extracts up to 120,000 characters (~30,000 tokens) around the segment section
- Primary model: `gpt-4o-mini` — falls back to `gpt-4o` if that fails
- Results are cached for 90 days

The final method label shows which approach was used (`xbrl`, `edgar+llm`, or `yahoo+llm`).

### Step 5 — Normalize segment names

The same segment may appear with slightly different names across filings:

> "Greater China" → "China" → "Mainland China, Hong Kong and Taiwan"

The normalizer uses fuzzy string matching (`rapidfuzz`) to group these into one consistent name. Names are compared using `token_sort_ratio`, which sorts words before comparing — so "Cloud Computing Services" and "Services Cloud Computing" both match correctly.

Per-ticker canonical name mappings are saved to `data/segments/{TICKER}_canonical.json`.

### Step 6 — Aggregate across periods

All periods are combined into a single JSON file per ticker:

```json
{
  "annual_periods":    ["FY2025", "FY2024", "FY2023"],
  "quarterly_periods": ["2026Q1", "2025Q4", ..., "2023Q2"],
  "segment_trend":     { "iPhone": [{ "period": "FY2023", "value": ... }, ...] },
  "ttm_revenue":       <sum of 4 most recent quarters>,
  "annual_count":      3,
  "quarterly_count":   10
}
```

### Step 7 — Fill quarterly gaps from Yahoo Finance

For US_SEC companies, SEC only requires 10-Q filings for the first three quarters of each fiscal year. The fourth quarter (fiscal year-end) only appears in the annual 10-K. To partially fill this gap, the system fetches Yahoo Finance quarterly data and adds any quarters not already covered by EDGAR.

Yahoo Finance retains approximately 5 recent quarters of data, so older fiscal-year-end quarters (older than ~15 months) cannot be recovered this way.

### Step 8 — Write business model analysis

For each company, the system calls the OpenAI API and generates a 400–600 word business model analysis covering:

- **Revenue Drivers** — main segments and how their contribution has changed over 3 years
- **Earnings & Profitability** — margins, cost structure, operating leverage
- **Business Model Summary** — how the company converts revenue into profit

The analysis is generated from the actual segment data, not from generic knowledge.

### Step 9 — Generate HTML reports

Each company gets a self-contained HTML file with:

- Interactive Sankey chart (drag nodes, scroll to zoom, period selector)
- Income statement table with year-over-year comparisons
- LLM-written business model analysis
- Data coverage notes explaining any gaps

A sortable dashboard (`index.html`) links to all 98 companies.

---

## Sankey chart

The Sankey shows the P&L flow across 6 layers from left to right:

```
Business segments
      │
      ▼
Total Revenue ──► Gross Profit ──► R&D / SG&A / Other OpEx ──► Operating Income ──► Tax / Interest ──► Net Income
                │
                └──► COGS (exits the flow)
```

**Design decisions:**

- Segments below 1.5% of revenue (or beyond the top 8) are merged into an "Other" bucket.
- All links have a minimum width of 1% of revenue so no flow disappears visually.
- If `gross_profit` is known but `cogs` is missing: `cogs = revenue − gross_profit` (and vice versa).
- Missing values are estimated from operating income + expense lines.

**Node colors:**

| Node type | Color |
|-----------|-------|
| Business segments | Blue |
| Revenue / profit | Green |
| Costs / losses | Red |
| R&D, SG&A | Orange |
| Tax, interest | Grey |

**Interactivity:** all periods are pre-built and embedded in the HTML. Switching periods uses `Plotly.react()` — no page reload, no external requests.

---

## Income statement table

### Annual view

Shows the two most recent fiscal years side by side with YoY change percentages.

### Quarterly view

Pairs each recent quarter with the same quarter of the prior year (e.g. 2025Q4 vs 2024Q4). Up to 4 pairs are shown as tabs. A pair is only shown if both quarters have data — if the prior-year quarter is missing, the pair is skipped rather than showing incomplete data.

YoY formula:
```
yoy% = (current − prior) / |prior| × 100
```

Using `|prior|` handles cases where the prior year was a loss.

**Confidence score (0.0 – 1.0):**

| Criterion | Points |
|-----------|--------|
| Total revenue present | +0.40 |
| Gross profit present | +0.10 |
| Operating income present | +0.15 |
| Net income present | +0.15 |
| Each detail line (cogs, R&D, SG&A, interest, tax) | +0.04 each, max +0.20 |
| 2+ segments | +0.30 |
| 1 segment | +0.10 |
| 0 segments | capped at 0.55 |

---

## Cache system

All API results are saved locally as JSON with a timestamp. Re-runs skip API calls and load from cache instead.

```json
{ "timestamp": 1714320000.0, "data": { ... } }
```

| Cache folder | TTL | Why |
|--------------|-----|-----|
| `checker/` | 30 days | Company classification rarely changes |
| `filings/` | 30 days | Filing lists update slowly |
| `yfinance/` | 7 days | Financial data updates weekly |
| `xbrl/` | 7 days | Minor XBRL amendments possible |
| `llm/` | 90 days | Expensive to re-run; filing text is stable |
| `segments/` | 30 days | Final aggregated output |

LLM cache keys include a hash of the prompt — so if the prompt changes, the cache is automatically invalidated.

---

## Configuration

Key settings in `config.py`:

```python
YEARS_BACK    = 3   # how many annual reports to fetch per company
QUARTERS_BACK = 12  # how many quarterly reports to target per company

SEC_RATE_LIMIT     = 8    # max SEC requests per second
SEC_RETRY_MAX      = 5    # max retries on failed requests
SEC_RETRY_BASE_SEC = 2.0  # starting delay for exponential backoff

MAX_TICKER_WORKERS = 3    # parallel tickers (keep low — SEC has a global cap)
MAX_LLM_WORKERS    = 20   # parallel LLM calls within one ticker

XBRL_COVERAGE_MIN  = 0.70  # if XBRL covers < 70% of revenue, use LLM instead

SEGMENT_MIN_PCT    = 0.005  # ignore segments smaller than 0.5% of revenue
REVENUE_WARN_DIFF  = 0.15   # warn if SEC and Yahoo revenue differ by > 15%

OPENAI_MODEL_EXTRACTION = "gpt-4o-mini"
OPENAI_MODEL_FALLBACK   = "gpt-4o"
FILING_TEXT_MAX_CHARS   = 120_000  # ~30k tokens sent to LLM
```

---

## Known limitations & data coverage notes

### Quarterly coverage

The system targets 12 quarterly reports per company (4 quarters × 3 years). In practice:

- **US companies reach 10–11 quarters.** SEC only requires 10-Q filings for the first 3 quarters of each fiscal year. The 4th quarter is only in the annual 10-K. Yahoo Finance fills in some of these missing quarters, but only retains ~5 recent quarters.
- **International companies reach 5–6 quarters.** Their SEC filings (6-K) do not follow a standard income statement format, so all quarterly P&L must come from Yahoo Finance — which means a maximum of ~5 quarters.

Each company report includes an automatically generated note explaining exactly why quarterly data is limited, so gaps are transparent rather than silent.

### Segment completeness

Segment data quality depends entirely on what is available in SEC filings:

- Companies that don't break down revenue by segment in their filings will have no segment data regardless of the method used.
- Some INTL_YAHOO companies (Samsung, LVMH, Nestlé, etc.) have no quarterly segments at all — they don't file with the SEC and Yahoo Finance doesn't provide granular segment breakdowns.

### Do not increase `MAX_TICKER_WORKERS` above 5

SEC EDGAR enforces a global limit of 10 requests per second across all concurrent threads. Exceeding this causes HTTP 429 errors and a temporary IP ban.

---

## Q&A — Presentation preparation

The following questions are likely to come up during the May 30 presentation.

---

**Q: Why don't you have 12 quarterly reports for every company?**

> "SEC only requires companies to file quarterly reports — called 10-Q — for the first three quarters of their fiscal year. The fourth quarter is never filed separately. It only appears inside the annual report. So we get that missing quarter from Yahoo Finance instead. But Yahoo Finance only keeps about five recent quarters in their database. That means older Q4 data — anything older than roughly 15 months — is simply not available from either source. We cannot get data that does not exist. Instead of hiding this gap, our program automatically generates a short explanation for each company showing exactly why the data is missing."

---

**Q: Why do international companies have even fewer quarters?**

> "International companies file something called a 6-K report with the SEC. Unlike the US 10-Q, the 6-K has no standard income statement format — each company writes it differently. So we cannot reliably extract quarterly profit and loss data from 6-K filings. We fall back entirely to Yahoo Finance for international quarterly data, which means we get at most five quarters per company."

---

**Q: How do you handle companies where segment data is incomplete?**

> "Our program has three layers. First, it tries to read the structured XBRL data directly from SEC filings — this is the most accurate method. If that fails or the data is too sparse, it sends the filing text to an LLM and asks it to extract the segment breakdown. If neither source has enough detail, the program still generates the Sankey chart using whatever data is available, and adds a note explaining the limitation. So no company is silently skipped — every gap is documented."

---

**Q: What data sources did you use?**

> "Only SEC EDGAR and Yahoo Finance, exactly as required. SEC EDGAR gives us annual reports and quarterly filings. Yahoo Finance fills in the gaps — mainly the fiscal year-end quarter and international quarterly data. No other sources were used."

---

**Q: How does the LLM fit into the pipeline?**

> "The LLM is used in three places. First, when XBRL segment data is missing or incomplete, we send the relevant section of the filing text to the LLM and ask it to extract the segment breakdown. Second, the LLM writes a 400–600 word business model summary for each company based on the actual financial data we collected. Third, when data coverage is limited, the LLM writes a short professional note explaining why — for example, explaining that Yahoo Finance does not maintain a full three-year quarterly history for a given company."

---

**Q: How do you make sure you are not using data that was not yet published at the time?**

> "All data comes directly from SEC filings and Yahoo Finance using the dates reported in those filings. We never use forward-looking data or data that was not publicly available at the time the report was filed. The pipeline fetches data by filing date, not by calendar date, so there is no look-ahead bias in the data itself."
