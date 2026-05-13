"""
Financial Analysis System — Task 1
Generates interactive Sankey charts + business model analysis for 98 stocks.

Usage:
  python main.py --tickers NVDA AAPL MSFT
  python main.py --tickers-file stocks.txt
  python main.py --all
  python main.py --tickers NVDA --no-cache --verbose

  #mô tả mục tiêu và cách dùng của coding này
"""
#core setup: import chỉ là mang hàm vào main.py để chạy
from __future__ import annotations
#tránh lỗi vòng phụ thuộc giữa các file khiến Python không thể load module theo thứ tự tuyến tính
import argparse #đọc CLI arguments
import sys #điều khiển runtime Python
import time #đo thời gian chạy, benchmark từng ticker
from concurrent.futures import ThreadPoolExecutor, as_completed #chạy nhiều ticker song song
from pathlib import Path #xử lý đường dẫn file, cross-platform (Windows/Linux/Mac)
from typing import Dict, List, Optional, Tuple #type hint, IDE autocomplete

# Add project root to path: kết nối toàn bộ hệ thống
#khởi tạo toàn bộ pipeline system + import tất cả modules theo đúng kiến trúc config→ ingestion → extraction → analysis → visualization
sys.path.insert(0, str(Path(__file__).parent)) #cho phép import module nội bộ, tránh lỗi ModuleNotFoundError: src

from config import (
    OPENAI_API_KEY, MAX_TICKER_WORKERS, YEARS_BACK,
    FILING_TYPES_US, FILING_TYPES_INTL,
) #lấy cấu hình hệ thống, vì tách config khỏi code logic nên dễ đổi cấu hình môi trường
from src.llm.provider import provider_status #kiểm tra OpenAI API, verify key trước khi chạy pipeline
from src.ingestion.ticker_loader import TickerInfo, load_tickers, print_classification_table #load danh sách cổ phiếu, đầu vào file excel
from src.ingestion.filing_router import (
    get_filings_for_ticker,
    get_yahoo_data, get_revenue_validation, summarize_coverage,
) #lấy data từ SEC + Yahoo, đây là “data ingestion core”
from src.ingestion.edgar_client import set_identity_from_env #khai báo user khi gọi SEC API, lấy từ .env
from src.extraction.extraction_router import extract_for_filing, extract_for_yahoo_only #chuyển raw data → SegmentData, vì SEC/Yahoo data không dùng trực tiếp được
from src.extraction.normalizer import normalize_segments #chuẩn hóa tên segment
from src.analysis.segment_aggregator import aggregate, load_cached, AggregatedCompanyData #tổng hợp dữ liệu nhiều kỳ
from src.analysis.business_model_writer import write_business_model #AI viết phân tích công ty
from src.visualization.report_generator import generate_report #tạo HTML + Sankey chart


# ── Result tracking ───────────────────────────────────────────────────────────
#tracking trạng thái xử lý từng ticker/ "report card cho từng ticker"
class ProcessResult: #chạy song song nhiều ticker → phải có “report riêng từng cái”

    def __init__(self, ticker: str):
        self.ticker   = ticker #lưu mã cp, biết kết quả thuộc công ty nào
        self.success  = False #trạng thái chạy có thành công không, 80 success / 98 failed 18
        self.output   = None #đường dẫn file HTML output, đường dẫn file HTML output
        self.error    = "" #lưu lỗi nếu pipeline fail, biết fail ở step nào
        self.method   = ""   # "xbrl-lấy từ SEC", "llm-AI extraction", "yahoo", "mixed", ghi lại cách dữ liệu được tạo ra để đánh giá chất lượng data
        self.coverage = {} #thống kê mức độ dữ liệu đầy đủ, kiểu "annual": 3,"quarterl": 8
        self.elapsed  = 0.0 #t0=0 để t1 trừ đi đo thời gian xử lý 1 ticker, để xem so với benchmark, detect ticker nào “nặng”


# ── Per-ticker pipeline ───────────────────────────────────────────────────────

#khung điều phối cho toàn bộ pipeline xử lý 1 cổ phiếu
def process_ticker( #định nghĩa “job xử lý 1 công ty”
    ticker_info: TickerInfo,  #thông tin công ty (NVDA, AAPL...)
    force_refetch: bool = False,  #có bỏ cache không
    verbose:       bool = False,  #có in chi tiết log ra màn hình hay không, false: chạy im lặng, true: hiện thi từng bước chạy ra màn hình, này là mặc định false
) -> ProcessResult:  #kết quả của 1 ticker
    result = ProcessResult(ticker_info.ticker) #tạo object lưu kết quả xử lý, mỗi ticker có output riêng
    t0 = time.time() #bắt đầu đo thời gian chạy, detect ticker nào chậm

    def log(msg: str): #hàm helper để in log(ghi lại trạng thái chạy chương trình) có format đẹp
        if verbose:
            print(f"  [{ticker_info.ticker}] {msg}") #msg là message, f-string/formattedstring cho phép nhét biến vào chuỗi

    try: #bắt lỗi toàn bộ pipeline bên trong, tránh crash toàn bộ system khi 1 ticker lỗi
        # Step 1 — Check if already processed and cached, kiểm tra xem ticker đã xử lý trước đó chưa → nếu có cache thì dùng luôn, không cần chạy toàn pipeline
        #fast path optimization (đường tắt để tăng tốc hệ thống)”
        if not force_refetch: #nếu KHÔNG ép chạy lại → mới dùng cache, ép chạy lại, k dùng cache
            cached_agg = load_cached(ticker_info.ticker) #đọc dữ liệu đã xử lý trước đó, tránh gọi API + LLM lại nhiều lần
            if cached_agg and cached_agg.annual_count > 0: #kiểm tra cache có hợp lệ không, cached_agg tồn tại
                log("Using cached aggregated data") #báo đang dùng cache vào log, dùng cho verbose
                analysis = write_business_model(cached_agg) #dùng LLM (dùng code từ src) viết phân tích từ dữ liệu cache,không cần ingestion/extraction lại
                out_path = generate_report(cached_agg, analysis) #tạo file HTML Sankey + report từ hai thứ trong ngoặc
                result.success  = True #đổi trạng thái ticker sang true để hệ thống ghi nhận
                result.output   = out_path #đổi đường dẫn file từ none sang đường dẫn file vào path chung trong config
                result.coverage = {"annual": cached_agg.annual_count, "quarterly": cached_agg.quarterly_count} #thống kê dữ liệu có bao nhiêu kỳ
                result.elapsed  = time.time() - t0 #đo thời gian chạy
                return result #thoát luôn function, KHÔNG chạy ingestion/extraction nữa

        # Step 2 — Fetch data: “INGESTION PHASE” thật sự trong pipeline”
        log("Fetching filings...") #báo đang lấy data vào log
        filings = get_filings_for_ticker(ticker_info) #Gọi function ingestion, Lấy data từ SEC, Trả kết quả về main.py dưới biến tạm fillings, sau khi chuyển ticker thì biến tạm sẽ lưu gtri mới

        log("Fetching Yahoo Finance data...")
        yahoo_data  = get_yahoo_data(ticker_info)
        revenue_map = get_revenue_validation(ticker_info)

        # Step 3 — Extract segment data per period (edgartools + LLM)
        all_segment_data = []

        if filings:
            log(f"Extracting from {len(filings)} filings...")
            for filing in filings:
                sd = extract_for_filing(
                    filing      = filing,
                    ticker_info = ticker_info,
                    revenue_map = revenue_map,
                )
                all_segment_data.append(sd)
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
        log("Aggregating...")
        agg = aggregate(
            ticker      = ticker_info.ticker,
            name        = ticker_info.name,
            all_periods = all_segment_data,
            sector      = ticker_info.sector,
        )

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
    # Annual periods (last 3 years)
    for year_offset in range(3):
        fy = datetime.date.today().year - year_offset
        period = f"FY{fy}"
        sd = extract_for_yahoo_only(
            ticker_info = ticker_info,
            period      = period,
            yahoo_data  = yahoo_data,
            is_annual   = True,
            fiscal_year = fy,
        )
        if sd.total_revenue:
            periods_data.append(sd)

    # Quarterly periods (last 8 quarters from Yahoo)
    qtr_income = yahoo_data.get("quarterly_income", {})
    for date_str in sorted(qtr_income.keys(), reverse=True)[:8]:
        try:
            import pandas as pd
            d = pd.Timestamp(date_str)
            q = (d.month - 1) // 3 + 1
            period = f"{d.year}Q{q}"
            sd = extract_for_yahoo_only(
                ticker_info = ticker_info,
                period      = period,
                yahoo_data  = yahoo_data,
                is_annual   = False,
                fiscal_year = d.year,
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