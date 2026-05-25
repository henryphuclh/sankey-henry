# Sankey Financial Analysis

Automated pipeline that fetches financial data for **98 global stocks**, extracts business segment breakdowns, and generates interactive HTML reports with Sankey flow charts, income statement tables, and LLM-written business model summaries.

**Live results (GitHub Pages):** `docs/sankey/index.html`

---

## Table of Contents

1. [Setup](#setup)
2. [How to run](#how-to-run)
3. [Project structure](#project-structure)
4. [Pipeline — detailed walkthrough](#pipeline--detailed-walkthrough)
   - [Stage 1 — Classify each ticker](#stage-1--classify-each-ticker)
   - [Stage 2 — Download filings](#stage-2--download-filings)
   - [Stage 3 — Extract P&L](#stage-3--extract-pl)
   - [Stage 4 — Extract business segments](#stage-4--extract-business-segments)
   - [Stage 5 — Aggregate & analyze](#stage-5--aggregate--analyze)
   - [Stage 6 — Generate HTML reports](#stage-6--generate-html-reports)
5. [Sankey chart](#sankey-chart)
6. [Income statement table](#income-statement-table)
7. [Cache system](#cache-system)
8. [Configuration](#configuration)
9. [Known limitations](#known-limitations)

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
| `--workers N` | Parallel tickers (default: 3 — keep low, SEC rate-limits globally) |
| `--output-dir PATH` | Override the HTML output folder |

After running, open `docs/sankey/index.html` in a browser to browse all companies.

**Re-running without `--no-cache`** skips all API calls and only regenerates HTML from the saved JSON files in `data/segments/`. This takes a few seconds.

---

## Project structure

```
.
├── main.py                          # Entry point — orchestrates the full pipeline
├── config.py                        # All global settings and thresholds
├── requirements.txt
├── Valuation_Top100_2026-04-18.xlsx # 98-ticker input list (ticker, sector, CIK)
│
├── src/
│   ├── ingestion/
│   │   ├── ticker_loader.py         # Reads Excel, classifies each ticker
│   │   ├── data_source_checker.py   # Looks up CIK and filing history on EDGAR
│   │   ├── edgar_client.py          # Downloads/parses SEC filings (XBRL + text)
│   │   ├── yahoo_client.py          # Downloads income statements from Yahoo Finance
│   │   ├── filing_router.py         # Picks the right data source per ticker
│   │   └── rate_limiter.py          # Limits SEC requests to 8/second
│   │
│   ├── extraction/
│   │   ├── models.py                # Data structures: FilingRecord, SegmentData, SegmentValue
│   │   ├── extraction_router.py     # XBRL-first extraction, falls back to LLM
│   │   ├── llm_extractor.py         # Sends filing text to OpenAI, parses segment JSON
│   │   ├── normalizer.py            # Fuzzy-matches segment names across periods
│   │   └── sector_handlers/
│   │       ├── financials.py        # P&L for banks and insurance companies
│   │       ├── pharma.py            # P&L for pharmaceutical companies
│   │       └── standard.py          # P&L for all other companies
│   │
│   ├── analysis/
│   │   ├── segment_aggregator.py    # Combines all periods → data/segments/{ticker}.json
│   │   ├── business_model_writer.py # LLM writes a 400–600 word business model analysis
│   │   └── coverage_explainer.py    # LLM writes a note explaining any data gaps
│   │
│   ├── visualization/
│   │   ├── sankey_builder.py        # Builds Sankey node/link structure (1-layer or 2-layer)
│   │   ├── sankey_renderer.py       # Renders Sankey as Plotly JSON
│   │   ├── report_generator.py      # Assembles the full HTML report per company
│   │   └── index_generator.py       # Builds the sortable index.html dashboard
│   │
│   ├── cache/
│   │   └── cache_manager.py         # JSON cache with per-namespace TTL
│   └── llm/
│       └── provider.py              # OpenAI wrapper with retry + fallback model
│
├── data/
│   ├── cache/
│   │   ├── checker/                 # Ticker classification (30-day TTL)
│   │   ├── filings/                 # SEC filing lists (30-day TTL)
│   │   ├── llm/                     # LLM outputs (90-day TTL)
│   │   ├── xbrl/                    # XBRL parsed data (7-day TTL)
│   │   └── yfinance/                # Yahoo Finance data (7-day TTL)
│   └── segments/                    # Final aggregated JSON per ticker
│
└── docs/
    └── sankey/                      # GitHub Pages output
        ├── index.html               # Dashboard — browse all 98 companies
        └── {TICKER}_sankey.html     # Per-company interactive report
```

---

## Pipeline — detailed walkthrough

### Stage 1 — Classify each ticker

Each ticker is checked against SEC EDGAR and assigned to one of three categories:

| Category | Count | Data source | Filing types |
|----------|-------|-------------|--------------|
| **US_SEC** | 74 | SEC EDGAR | 10-K (annual), 10-Q (quarterly) |
| **INTL_SEC** | 15 | SEC EDGAR | 20-F or 40-F (annual), 6-K (quarterly) |
| **INTL_YAHOO** | 9 | Yahoo Finance only | — |

- **40-F** is the Canadian equivalent of 20-F, used by RY.TO and TD.TO.
- **INTL_YAHOO** companies (Samsung, SK Hynix, Tencent, Roche, LVMH, Nestlé, Siemens, CBA, Allianz) do not file with the SEC, so all data comes from Yahoo Finance.

Each company is also assigned a **sector** based on its SIC code:

| Sector | Count | Notes |
|--------|-------|-------|
| **standard** | 65 | Regular companies — standard income statement |
| **financial** | 20 | Banks and insurers — net interest income structure |
| **pharma** | 13 | Pharmaceutical and biotech |

Results are cached for 30 days in `data/cache/checker/`.

---

### Stage 2 — Download filings

For US_SEC and INTL_SEC companies, the pipeline fetches the list of recent filings from SEC EDGAR — up to 3 years of annual reports and 12 quarters. Each filing is stored as a `FilingRecord` (ticker, form type, period label, accession number, CIK). Filing lists are cached for 30 days.

For INTL_YAHOO companies, only Yahoo Finance data is fetched at this stage.

---

### Stage 3 — Extract P&L

The income statement is read from each EDGAR filing using **XBRL** — a structured data format all public companies must include in their SEC filings. The system maps XBRL concepts to a standard set of metrics:

| Metric | US-GAAP XBRL concept |
|--------|----------------------|
| Total revenue | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Gross profit | `GrossProfit` |
| Operating income | `OperatingIncomeLoss` |
| Net income | `NetIncomeLoss` |
| COGS | `CostOfRevenue`, `CostOfGoodsSold` |
| R&D expense | `ResearchAndDevelopmentExpense` |
| SG&A expense | `SellingGeneralAndAdministrativeExpense` |
| Interest expense | `InterestExpense` |
| Income tax | `IncomeTaxExpense` |

For banks and insurers, a separate sector handler uses bank-specific concepts: net interest income, noninterest income, provision for credit losses, etc.

Yahoo Finance data serves as a **cross-validation source** — if the EDGAR revenue and Yahoo revenue differ by more than 15%, a warning is attached to the period. For international companies with non-USD reporting currencies, Yahoo Finance also provides the USD exchange rate used to convert all values.

---

### Stage 4 — Extract business segments

This is the core step. For each filing, the system tries three methods in order:

#### Method 1 — XBRL dimensions (67 companies)

Business segments in EDGAR filings are tagged along a "dimension axis." The system searches for axes in priority order:

| Priority | Axis | Description |
|----------|------|-------------|
| 1 | `us-gaap:StatementBusinessSegmentsAxis` | ASC 280 operating segments (MSFT, AMZN, NVDA…) |
| 2 | `ifrs-full:SegmentsAxis` | IFRS IAS 8 segments (NOVO-B.CO, SAP.DE…) |
| 3 | `srt:ConsolidationItemsAxis` | Consolidation-items axis (some AAPL periods) |
| 4 | `srt:ProductOrServiceAxis` | Product/service lines (NVDA, AMZN products) |
| 5 | `srt:StatementGeographicalAxis` | Geographic fallback |

**Special logic for mixed-geo axes** — when the business segments axis contains both geographic members ("North America", "International") and non-geographic members ("AWS"), the system switches to the `ProductOrServiceAxis` if it provides more non-geographic members. This is why AMZN shows 7 product lines (Online stores, AWS, Advertising…) instead of 3 geographic segments.

**Subtotal detection** — if the segment sum exceeds total revenue by >10%, the system iteratively removes the largest member until the sum falls within ±15%. This removes "Total" rollup rows that some companies include alongside individual segments.

**Minimum size filter** — segments smaller than 0.5% of revenue or under $50M are dropped to avoid XBRL sub-line artifacts.

#### Method 2 — `dimension_label` scan

Some companies (MRK, RY.TO) store segment names inside a `dimension_label` text field rather than a proper axis. When Method 1 finds nothing, the pipeline scans this field for patterns like `StatementBusinessSegmentsAxis: <SegmentName>`.

#### Method 3 — LLM extraction (22 companies)

When XBRL coverage falls below 70% of total revenue, the pipeline extracts the "Segment Information" note from the filing text and sends it to OpenAI:

- Extracts the relevant note section (~120,000 characters)
- Model: `gpt-4o-mini` with `gpt-4o` fallback
- Returns structured segment names + values in JSON
- Results cached for 90 days

**Revenue-note fallback** — for companies like GOOGL where the segment note returns only top-level categories (Google Services, Google Cloud, Other Bets), the pipeline also tries the Revenue/MD&A note, which may contain finer-grained product breakdowns. The finer result is accepted if it has more segments and covers >50% of revenue.

**Subtotal stripping** — after LLM extraction, if the segment sum is inflated (e.g. the LLM returned a "Google advertising" subtotal alongside Search + YouTube + Network), the largest item is removed if removing it makes the sum match total revenue within ±15%.

---

#### 2-layer hierarchy (MSFT)

Microsoft reports both ASC-280 operating segments and ASC-606 product disaggregation in XBRL. The pipeline detects this and builds a **2-layer Sankey**:

```
Microsoft 365 Commercial ($87.8B) ──►
Microsoft 365 Consumer   ($7.4B)  ──► Productivity & BP ($120.8B) ──►
LinkedIn                 ($17.8B) ──►                                  ►
Dynamics                 ($7.8B)  ──►                                  ► Revenue ($281.7B)

Server Products          ($98.4B) ──► Intelligent Cloud ($106.3B) ──►  ►
Enterprise Services      ($7.8B)  ──►

Windows & Devices        ($17.3B) ──►
Gaming                   ($23.5B) ──► More Personal Computing ($54.6B) ──►
Search & News Advertising($13.9B) ──►
```

The mapping between sub-products and their parent segment is extracted from the "Segment Information" note's bullet-point structure — no LLM required for this step. Any company that (a) has both XBRL axes, (b) where product items are not flagged as pure revenue disaggregation, and (c) whose filing note uses "•" bullets to list products under each segment, will automatically get the 2-layer chart.

---

#### Segment name normalization

The same segment may appear under slightly different names across years:

> "Greater China" → "China" → "Mainland China, Hong Kong and Taiwan"

The normalizer uses `rapidfuzz` fuzzy matching (`token_sort_ratio`, threshold 80) to group these into one consistent canonical name. Per-ticker mappings are saved to `data/segments/{TICKER}_canonical.json`.

---

### Stage 5 — Aggregate & analyze

#### 5a — Segment aggregator

All individual `SegmentData` objects (one per filing period) are combined into a single JSON file per ticker at `data/segments/{ticker}.json`:

```json
{
  "ticker": "AAPL",
  "latest_annual": { "period": "FY2024", "segments": [...], "total_revenue": ... },
  "annual_periods": [...],
  "quarterly_periods": [...],
  "segment_trend": { "iPhone": [{ "period": "FY2022", "value": 205489000000 }, ...] },
  "ttm_revenue": 391035000000,
  "annual_count": 3,
  "quarterly_count": 10
}
```

TTM (trailing twelve months) is computed from the 4 most recent quarters.

Amended filings (10-K/A, 10-Q/A) are deduplicated — only the version with the highest confidence score is kept per period.

#### 5b — Business model writer

For each company, the pipeline calls OpenAI and generates a 400–600 word business model analysis covering three sections:

- **Revenue Drivers** — which segments drive revenue and how their mix has shifted over 3 years
- **Earnings & Profitability** — margin structure, cost drivers, operating leverage
- **Business Model Summary** — how the company converts revenue into net income

The analysis is generated from the actual extracted data, not from the model's general knowledge. Results are cached by a hash of the data summary — if the underlying data changes, the narrative is automatically regenerated.

---

### Stage 6 — Generate HTML reports

Each company gets a self-contained HTML file built from:

- **Sankey chart** — interactive Plotly diagram (see below)
- **Income statement table** — annual and quarterly views with YoY changes
- **Business model analysis** — the LLM-written narrative from Stage 5b
- **Coverage notes** — automatically generated text explaining any data gaps

A sortable, searchable **index dashboard** (`index.html`) links to all 98 companies and shows key metrics (revenue, net income, margin, segment count, data source).

---

## Sankey chart

### Standard flow (non-financial companies)

```
[Sub-products]     [Top Segments]   [Revenue]  [Gross Profit]  [OpEx]   [Op. Income]  [Tax/Int]  [Net Income]
    x=0.00     →      x=0.13     →   x=0.25  →    x=0.40    →  x=0.60 →    x=0.75  →   x=0.87 →    x=1.00
```

The sub-products layer (x=0.00 → top segments at x=0.13) only appears for companies with a 2-layer hierarchy (currently MSFT). All other companies start with top segments at x=0.00.

```
[Segments]  [Revenue]  [Gross Profit]  [R&D / SG&A / Other]  [Op. Income]  [Tax / Interest]  [Net Income]
  x=0.00  →  x=0.20 →    x=0.40     →        x=0.60        →    x=0.75   →     x=0.87      →    x=1.00
```

COGS flows from Revenue downward and exits the diagram (it is a cost, not a profit node).

### Bank flow

Banks use a different structure because they don't have COGS:

```
[Segments]  [Net Revenue]  [Provision]  [Revenue after Provision]  [NonInt. Expenses]  [Op. Income]  [Net Income]
```

### Design rules

- Segments below **1.5% of revenue** or beyond the top **8** are merged into "Other".
- All links have a **minimum width** of 1% of revenue so no flow disappears visually.
- If `gross_profit` is known but `cogs` is missing: `cogs = revenue − gross_profit` (and vice versa).
- When operating income is unknown, it is inferred from `net_income + tax + interest`.
- **Link scaling** — when segment sums slightly exceed revenue (due to inter-segment eliminations), all link widths are scaled proportionally. Labels always show the real filing values.

### Node colors

| Node type | Color |
|-----------|-------|
| Business segments (top) | Distinct palette (blue, orange, green…) |
| Sub-products | Same color as their parent segment |
| Revenue / profit nodes | Green |
| Cost / loss nodes | Red |
| R&D, SG&A, OpEx | Orange |
| Tax, interest, other | Grey |

**Interactivity** — all periods are pre-built and embedded in the HTML. Switching periods uses `Plotly.react()` with no page reload and no external requests.

---

## Income statement table

### Annual view

Shows the two most recent fiscal years side by side with YoY % change.

### Quarterly view

Pairs each recent quarter with the same quarter of the prior year (e.g. 2025Q4 vs 2024Q4). Up to 4 pairs shown as tabs. A pair is only shown if both quarters have data.

YoY formula uses `|prior|` as the denominator to handle cases where the prior year was a loss:
```
yoy% = (current − prior) / |prior| × 100
```

### Confidence score (0.0 – 1.0)

| Criterion | Points |
|-----------|--------|
| Total revenue present | +0.40 |
| Operating income present | +0.15 |
| Net income present | +0.15 |
| Gross profit present | +0.10 |
| Each detail line (cogs, R&D, SG&A, interest, tax) | +0.04 each, capped at +0.20 |
| 2+ segments | +0.30 |
| 1 segment | +0.10 |
| 0 segments | score capped at 0.55 |

---

## Cache system

All API responses are saved as JSON with a timestamp. Re-runs load from cache and skip API calls entirely.

```json
{ "timestamp": 1714320000.0, "data": { ... } }
```

| Cache folder | TTL | Reason |
|--------------|-----|--------|
| `data/cache/checker/` | 30 days | Company classification rarely changes |
| `data/cache/filings/` | 30 days | Filing lists update slowly |
| `data/cache/yfinance/` | 7 days | Financial data updates weekly |
| `data/cache/xbrl/` | 7 days | Minor XBRL amendments possible |
| `data/cache/llm/` | 90 days | Expensive to re-run; filing text is stable |

LLM cache keys include a **hash of the prompt** — if the prompt or extracted data changes, the cache is automatically invalidated without manual clearing.

---

## Configuration

Key settings in `config.py`:

```python
YEARS_BACK    = 3   # annual reports per company
QUARTERS_BACK = 12  # quarterly reports to target per company

SEC_RATE_LIMIT     = 8    # max SEC requests per second
SEC_RETRY_MAX      = 5    # max retries on 429/503
SEC_RETRY_BASE_SEC = 2.0  # starting delay for exponential backoff

MAX_TICKER_WORKERS = 3    # parallel tickers (keep ≤ 5 — SEC has a global 10 req/s cap)
MAX_LLM_WORKERS    = 20   # parallel LLM calls within one ticker batch

XBRL_COVERAGE_MIN  = 0.70  # if XBRL covers < 70% of revenue, fall back to LLM
SEGMENT_MIN_PCT    = 0.005  # drop segments < 0.5% of revenue
SEGMENT_MIN_VALUE  = 50_000_000  # drop segments < $50M absolute

REVENUE_WARN_DIFF  = 0.15   # warn if SEC and Yahoo revenue differ by > 15%

OPENAI_MODEL_EXTRACTION = "gpt-4o-mini"
OPENAI_MODEL_FALLBACK   = "gpt-4o"
FILING_TEXT_MAX_CHARS   = 120_000  # ~30k tokens sent to the LLM per call
```

---

## Known limitations

### Quarterly coverage

The pipeline targets 12 quarterly reports per company (4 quarters × 3 years). In practice:

- **US companies reach 10–11 quarters.** SEC requires 10-Q filings for only the first 3 quarters of each fiscal year. The 4th quarter appears exclusively in the annual 10-K. Yahoo Finance fills some missing quarters, but retains only ~5 recent quarters.
- **International companies reach 5–6 quarters.** Their 6-K filings have no standard income statement format, so all quarterly P&L comes from Yahoo Finance — a maximum of ~5 quarters.

Each company report includes an automatically generated note explaining exactly why quarterly data is limited.

### Segment data for INTL_YAHOO companies

Samsung, SK Hynix, Tencent, Roche, LVMH, Nestlé, Siemens, CBA, and Allianz do not file with the SEC. Yahoo Finance does not provide granular segment breakdowns, so these 9 companies have P&L data but no segment chart. The Sankey shows the income flow without a segment layer.

### Do not increase `MAX_TICKER_WORKERS` above 5

SEC EDGAR enforces a global limit of 10 requests per second across all concurrent connections. Exceeding this causes HTTP 429 errors and a temporary IP ban that affects all threads.
