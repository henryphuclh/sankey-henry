"""
Financial Analysis System — Task 1
Generates interactive Sankey charts + business model analysis for 98 stocks.

Usage:
  python main.py --tickers NVDA AAPL MSFT
  python main.py --tickers-file stocks.txt
  python main.py --all
  python main.py --tickers NVDA --no-cache --verbose
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    OPENAI_API_KEY, MAX_TICKER_WORKERS, YEARS_BACK, QUARTERS_BACK,
    FILING_TYPES_US, FILING_TYPES_INTL,
)
from src.llm.provider import provider_status
from src.ingestion.ticker_loader import TickerInfo, load_tickers, print_classification_table
from src.ingestion.filing_router import (
    get_filings_for_ticker,
    get_yahoo_data, get_revenue_validation, summarize_coverage,
)
from src.ingestion.edgar_client import set_identity_from_env
from src.extraction.extraction_router import extract_for_filing, extract_for_yahoo_only, fill_quarterly_gaps_from_yahoo
from src.extraction.normalizer import normalize_segments
from src.analysis.segment_aggregator import aggregate, load_cached, AggregatedCompanyData
from src.analysis.business_model_writer import write_business_model
from src.visualization.report_generator import generate_report


# ── Result tracking ───────────────────────────────────────────────────────────

class ProcessResult:

    def __init__(self, ticker: str):
        self.ticker   = ticker
        self.success  = False
        self.output   = None
        self.error    = ""
        self.method   = ""
        self.coverage = {}
        self.elapsed  = 0.0


# ── Per-ticker pipeline ───────────────────────────────────────────────────────

def process_ticker(
    ticker_info: TickerInfo,
    force_refetch: bool = False,
    verbose:       bool = False,
) -> ProcessResult:
    result = ProcessResult(ticker_info.ticker)
    t0 = time.time()

    def log(msg: str):
        if verbose:
            print(f"  [{ticker_info.ticker}] {msg}")

    try:
        # Step 1 — Check if already processed and cached
        if not force_refetch:
            cached_agg = load_cached(ticker_info.ticker)
            if cached_agg and cached_agg.annual_count > 0:
                log("Using cached aggregated data")
                cached_agg.classification = ticker_info.classification
                # Apply quarterly gap-fills from Yahoo even on the cached path
                # (yfinance data is locally cached so this is fast)
                if ticker_info.classification == "US_SEC":
                    _yahoo_data = get_yahoo_data(ticker_info)
                    _all_sds    = cached_agg.annual_periods + cached_agg.quarterly_periods
                    _gap_fills  = fill_quarterly_gaps_from_yahoo(_all_sds, _yahoo_data, ticker_info)
                    if _gap_fills:
                        _all_sds = normalize_segments(_all_sds + _gap_fills, ticker_info.ticker)
                        cached_agg = aggregate(
                            ticker      = cached_agg.ticker,
                            name        = cached_agg.name,
                            all_periods = _all_sds,
                            sector      = cached_agg.sector,
                        )
                        cached_agg.classification = ticker_info.classification
                analysis = write_business_model(cached_agg)
                out_path = generate_report(cached_agg, analysis)
                result.success  = True
                result.output   = out_path
                result.coverage = {"annual": cached_agg.annual_count, "quarterly": cached_agg.quarterly_count}
                result.elapsed  = time.time() - t0
                return result

        # Step 2 — Fetch data
        log("Fetching filings...")
        filings = get_filings_for_ticker(ticker_info)

        log("Fetching Yahoo Finance data...")
        yahoo_data  = get_yahoo_data(ticker_info)
        revenue_map = get_revenue_validation(ticker_info)

        # Step 3 — Extract segment data per period (edgartools + LLM)
        all_segment_data = []

        if filings:
            log(f"Extracting from {len(filings)} filings...")
            # For INTL_SEC: 6-K quarterly filings have no structured income statement —
            # process only annual filings from EDGAR and synthesize quarterly from Yahoo.
            _intl_sec = ticker_info.classification == "INTL_SEC"
            annual_filings    = [f for f in filings if f.is_annual]
            quarterly_filings = [f for f in filings if not f.is_annual]
            process_filings   = annual_filings if _intl_sec else filings
            for filing in process_filings:
                sd = extract_for_filing(
                    filing      = filing,
                    ticker_info = ticker_info,
                    revenue_map = revenue_map,
                    yahoo_data  = yahoo_data,
                )
                all_segment_data.append(sd)
            if _intl_sec:
                # Quarterly: use Yahoo Finance dates directly (6-K period labels are
                # unreliable — filing date ≠ financial period end date).
                yahoo_quarters = _extract_yahoo_quarters(ticker_info, yahoo_data)
                all_segment_data.extend(yahoo_quarters)
            else:
                # US_SEC: 10-Q covers Q1-Q3 only; fill Q4 (and any other gaps)
                # from Yahoo Finance quarterly P&L data.
                gap_fills = fill_quarterly_gaps_from_yahoo(all_segment_data, yahoo_data, ticker_info)
                all_segment_data.extend(gap_fills)
        else:
            # INTL_YAHOO — synthesize periods from Yahoo data
            log("No SEC filings — using Yahoo Finance for all periods")
            all_segment_data = _extract_from_yahoo_all_periods(
                ticker_info, yahoo_data
            )

        if not all_segment_data:
            result.error = "No data extracted for any period"
            result.elapsed = time.time() - t0
            return result

        # Step 4 — Normalize segment names
        log("Normalizing segment names...")
        all_segment_data = normalize_segments(all_segment_data, ticker_info.ticker)

        # Step 5 — Aggregate
        # If ALL annual periods fell back to standard, use "standard" as the sector
        # so the Sankey and income statement use standard format for this company.
        effective_sector = ticker_info.sector
        annual_sds = [sd for sd in all_segment_data if sd.is_annual]
        if annual_sds:
            fallback_notes = [
                n for sd in annual_sds for n in (sd.notes or [])
                if n.startswith("SECTOR_FALLBACK:")
            ]
            if len(fallback_notes) == len(annual_sds):
                effective_sector = fallback_notes[0].split(":", 1)[1]

        log("Aggregating...")
        agg = aggregate(
            ticker      = ticker_info.ticker,
            name        = ticker_info.name,
            all_periods = all_segment_data,
            sector      = effective_sector,
        )
        agg.classification = ticker_info.classification

        # Step 6 — Business model narrative
        log("Writing business model analysis...")
        analysis = write_business_model(agg)

        # Step 7 — Generate HTML report
        log("Generating HTML report...")
        out_path = generate_report(agg, analysis)

        # Determine extraction method
        methods = {sd.extraction_method for sd in all_segment_data}
        result.method = "+".join(sorted(methods))

        result.success  = True
        result.output   = out_path
        result.coverage = summarize_coverage(ticker_info, filings)

    except Exception as e:
        result.error = str(e)
        if verbose:
            import traceback
            traceback.print_exc()

    result.elapsed = time.time() - t0
    return result


def _extract_from_yahoo_all_periods(
    ticker_info: TickerInfo,
    yahoo_data:  Dict,
) -> list:
    """For INTL_YAHOO tickers, synthesize SegmentData from Yahoo Finance for available periods."""
    from src.extraction.extraction_router import extract_for_yahoo_only
    import datetime

    periods_data = []
    annual_income = yahoo_data.get("annual_income", {})
    # Annual periods (last 3 years)
    for year_offset in range(YEARS_BACK):
        fy = datetime.date.today().year - year_offset
        period = f"FY{fy}"
        # Match fiscal year to the income date key so per-date forex rate is used
        date_key = next((d for d in sorted(annual_income, reverse=True) if d.startswith(str(fy))), None)
        if date_key is None:
            # Non-calendar fiscal year: try year-1 (e.g. FY ending Jan of fy)
            date_key = next((d for d in sorted(annual_income, reverse=True) if d.startswith(str(fy - 1))), None)
        sd = extract_for_yahoo_only(
            ticker_info    = ticker_info,
            period         = period,
            yahoo_data     = yahoo_data,
            is_annual      = True,
            fiscal_year    = fy,
            yahoo_date_key = date_key,
        )
        if sd.total_revenue:
            periods_data.append(sd)

    # Quarterly periods (last 8 quarters from Yahoo)
    qtr_income = yahoo_data.get("quarterly_income", {})
    for date_str in sorted(qtr_income.keys(), reverse=True)[:QUARTERS_BACK]:
        try:
            import pandas as pd
            d = pd.Timestamp(date_str)
            q = (d.month - 1) // 3 + 1
            period = f"{d.year}Q{q}"
            sd = extract_for_yahoo_only(
                ticker_info    = ticker_info,
                period         = period,
                yahoo_data     = yahoo_data,
                is_annual      = False,
                fiscal_year    = d.year,
                yahoo_date_key = date_str,
            )
            if sd.total_revenue:
                periods_data.append(sd)
        except Exception:
            continue

    return periods_data


def _extract_yahoo_quarters(
    ticker_info: TickerInfo,
    yahoo_data:  Dict,
) -> list:
    """Return quarterly SegmentData from Yahoo Finance for INTL_SEC tickers.

    INTL_SEC companies file 6-K for quarterly reports, but the 6-K period_of_report
    date is typically the filing date (e.g. April), not the quarter-end date (March 31).
    This mismatch makes period labels unreliable.  Instead, drive quarterly periods
    directly from the Yahoo quarterly income dates — same approach as INTL_YAHOO.
    """
    from src.extraction.extraction_router import extract_for_yahoo_only

    qtr_income = yahoo_data.get("quarterly_income", {})
    periods_data = []
    for date_str in sorted(qtr_income.keys(), reverse=True)[:QUARTERS_BACK]:
        try:
            import pandas as pd
            d = pd.Timestamp(date_str)
            q = (d.month - 1) // 3 + 1
            period = f"{d.year}Q{q}"
            sd = extract_for_yahoo_only(
                ticker_info    = ticker_info,
                period         = period,
                yahoo_data     = yahoo_data,
                is_annual      = False,
                fiscal_year    = d.year,
                yahoo_date_key = date_str,
            )
            if sd.total_revenue:
                periods_data.append(sd)
        except Exception:
            continue
    return periods_data


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Sankey charts + business model analysis for stocks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --tickers NVDA AAPL MSFT AMZN GOOGL TSLA META JPM LLY TSM
  python main.py --all
  python main.py --tickers-file my_stocks.txt
  python main.py --tickers NVDA --no-cache --verbose
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tickers",      nargs="+", metavar="TICKER",
                       help="Space-separated list of tickers")
    group.add_argument("--tickers-file", metavar="FILE",
                       help="Text file with one ticker per line")
    group.add_argument("--all",          action="store_true",
                       help="Process all 98 tickers in the universe")

    parser.add_argument("--no-cache",    action="store_true",
                        help="Bypass cache and re-fetch all data")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed per-ticker progress")
    parser.add_argument("--workers",     type=int, default=MAX_TICKER_WORKERS,
                        help=f"Concurrent tickers (default: {MAX_TICKER_WORKERS})")
    parser.add_argument("--output-dir",  type=str, default=None,
                        help="Output directory for HTML files")
    return parser.parse_args()


def resolve_tickers(args, universe: Dict[str, TickerInfo]) -> List[TickerInfo]:
    """Return the list of TickerInfo objects to process."""
    if args.all:
        return list(universe.values())

    if args.tickers_file:
        raw = Path(args.tickers_file).read_text(encoding="utf-8").strip().splitlines()
        requested = [t.strip().upper() for t in raw if t.strip()]
    else:
        requested = [t.upper() for t in args.tickers]

    result = []
    not_found = []
    for t in requested:
        if t in universe:
            result.append(universe[t])
        else:
            not_found.append(t)

    if not_found:
        print(f"\n⚠  Tickers not in universe (skipped): {not_found}")
        print(f"   Valid tickers: see Valuation_Top100_2026-04-18.xlsx\n")

    return result


def main():
    args = parse_args()

    print("\n" + "=" * 60)
    print("  Financial Analysis System — Task 1")
    print("=" * 60)

    # Register SEC EDGAR identity (required by edgartools)
    set_identity_from_env()

    # Show provider status
    pstatus = provider_status()
    print(f"\n  LLM Provider: OPENAI")
    if not pstatus["openai_key_set"]:
        print("\n[!] OPENAI_API_KEY not set!")
        print("   1. Go to https://platform.openai.com/api-keys")
        print("   2. Add OPENAI_API_KEY=sk-... to task1/.env\n")
    else:
        print(f"  Model:        {pstatus['models']['extraction']} "
              f"(fallback: {pstatus['models']['fallback']})")

    # Load universe
    universe = load_tickers()
    print(f"\n  Universe: {len(universe)} tickers loaded")

    # Resolve requested tickers
    tickers_to_process = resolve_tickers(args, universe)
    if not tickers_to_process:
        print("  No valid tickers to process. Exiting.")
        sys.exit(1)

    print(f"  Processing: {len(tickers_to_process)} tickers")
    print(f"  Workers:    {args.workers}")
    print(f"  Cache:      {'disabled' if args.no_cache else 'enabled'}")
    print()

    # Override output directory if specified
    if args.output_dir:
        import config
        config.OUTPUT_DIR = Path(args.output_dir)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Process with thread pool + progress bar
    results: List[ProcessResult] = []
    force_refetch = args.no_cache

    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(tickers_to_process), unit="ticker", ncols=70)
    except ImportError:
        pbar = None

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_ticker, ti, force_refetch, args.verbose): ti
            for ti in tickers_to_process
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if pbar:
                status = "✓" if result.success else "✗"
                pbar.set_postfix_str(f"{status} {result.ticker}")
                pbar.update(1)
            elif args.verbose:
                status = "✓" if result.success else "✗"
                print(f"  {status} {result.ticker} ({result.elapsed:.1f}s)")

    if pbar:
        pbar.close()

    # Summary
    successes = [r for r in results if r.success]
    failures  = [r for r in results if not r.success]
    xbrl_count = sum(1 for r in successes if "xbrl" in r.method)
    llm_count  = sum(1 for r in successes if "llm" in r.method)

    print(f"\n{'='*60}")
    print(f"  Results: {len(successes)}/{len(results)} companies processed successfully")
    print(f"  XBRL:    {xbrl_count} | LLM: {llm_count} | Mixed: {len(successes)-xbrl_count-llm_count}")
    if failures:
        print(f"\n  Failed tickers:")
        for r in failures:
            print(f"    ✗ {r.ticker}: {r.error}")

    if successes:
        from config import OUTPUT_DIR
        # Build / refresh the dashboard index
        try:
            from src.visualization.index_generator import build_index
            idx_path = build_index(OUTPUT_DIR)
            print(f"\n  Dashboard: {idx_path}")
        except Exception as e:
            print(f"\n  (index generation failed: {e})")
        print(f"  Output HTML files in: {OUTPUT_DIR}")
        print(f"  Open index.html to browse all companies.")

    print(f"{'='*60}\n")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())