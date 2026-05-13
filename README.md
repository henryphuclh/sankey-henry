# Financial Analysis System - Task 1

Hệ thống phân tích tài chính tự động cho 98 cổ phiếu toàn cầu. Hệ thống lấy dữ liệu từ SEC EDGAR và Yahoo Finance, trich xuất phân khúc kinh doanh bằng XBRL và LLM, tính toán các chỉ số tài chính, rồi sinh ra báo cáo HTML tương tác với biểu đồ Sankey và bảng Income Statement có so sánh YoY.


## Mục lục

1. [Cài đặt](#cài-đặt)
2. [Cách chạy](#cách-chạy)
3. [Cấu trúc thư mục](#cấu-trúc-thư-mục)
4. [Quy trình xử lý chi tiết](#quy-trình-xử-lý-chi-tiết)
5. [Tính toán chỉ số tài chính](#tính-toán-chỉ-số-tài-chính)
6. [Xây dựng biểu đồ Sankey](#xây-dựng-biểu-đồ-sankey)
7. [Bảng Income Statement và YoY](#bảng-income-statement-và-yoy)
8. [Hệ thống cache](#hệ-thống-cache)
9. [Cấu hình nâng cao](#cấu-hình-nâng-cao)
10. [Lưu ý vận hành](#lưu-ý-vận-hành)


## Cài đặt

Yêu cầu Python 3.8 trở lên.

```bash
pip install -r requirements.txt
```

Tạo file `.env` trong thư mục `task1/` (xem `.env.example` làm mẫu):

```
OPENAI_API_KEY=sk-YOUR_KEY_HERE
SEC_USER_AGENT=HoTen Email@example.com
```

- `OPENAI_API_KEY`: Lấy tại https://platform.openai.com/api-keys. Chi phí ước tính toàn bộ 98 mã cổ phiếu khoảng 1-3 USD khi dùng gpt-4o-mini.
- `SEC_USER_AGENT`: Bắt buộc theo quy định của SEC EDGAR. Ghi tên và email thực để tránh bị chặn IP.


## Cách chạy

```bash
cd task1

# Xử lý một vài mã cổ phiếu cụ thể
python main.py --tickers NVDA AAPL MSFT AMZN

# Xử lý từ file danh sách (mỗi dòng một ticker)
python main.py --tickers-file stocks.txt

# Xử lý toàn bộ 98 mã
python main.py --all
```

Các tùy chọn bổ sung:

| Tùy chọn | Giải thích |
|----------|------------|
| `--no-cache` | Bỏ qua cache, lấy lại toàn bộ dữ liệu từ nguồn |
| `--verbose` / `-v` | Hiển thị chi tiết quá trình xử lý từng ticker |
| `--workers N` | Số lượng ticker xử lý song song (mặc định: 3) |
| `--output-dir PATH` | Thư mục lưu file HTML đầu ra |

Sau khi chạy xong, mở `output/sankey/index.html` trong trình duyệt để xem dashboard.

Khi chạy lại mà không có `--no-cache`, hệ thống đọc JSON đã lưu trong `data/segments/` và chỉ render lại HTML. Không gọi thêm bất kỳ API nào, nên rất nhanh.


## Cấu trúc thư mục

```
task1/
|-- main.py                          # Điểm vào CLI, điều phối toàn bộ pipeline
|-- config.py                        # Cấu hình toàn cục
|-- requirements.txt
|-- .env.example
|-- Valuation_Top100_2026-04-18.xlsx # Danh sách 98 mã cổ phiếu (nguồn gốc thứ tự)
|
|-- src/
|   |-- ingestion/
|   |   |-- ticker_loader.py         # Đọc Excel, phân loại US_SEC / INTL_SEC / INTL_YAHOO
|   |   |-- edgar_client.py          # Gọi SEC EDGAR, parse P&L từ edgartools
|   |   |-- yahoo_client.py          # Gọi Yahoo Finance API
|   |   `-- filing_router.py         # Định tuyến nguồn dữ liệu theo loại ticker
|   |
|   |-- extraction/
|   |   |-- models.py                # Dataclass SegmentData, SegmentValue, FilingRecord
|   |   |-- extraction_router.py     # Điều phối XBRL + LLM, ghép kết quả
|   |   |-- llm_extractor.py         # Gọi OpenAI để trich xuất phân khúc từ văn bản
|   |   |-- normalizer.py            # Chuẩn hóa tên phân khúc bằng fuzzy matching
|   |   `-- sector_handlers/         # Xử lý đặc thù theo ngành (tài chính, pharma, tập đoàn)
|   |
|   |-- analysis/
|   |   |-- segment_aggregator.py    # Tổng hợp dữ liệu đa kỳ, tính TTM, segment trend
|   |   `-- business_model_writer.py # Gọi LLM viết narrative phân tích mô hình kinh doanh
|   |
|   |-- visualization/
|   |   |-- sankey_builder.py        # Xây dựng 6 lớp P&L flow từ SegmentData
|   |   |-- sankey_renderer.py       # Render Sankey bằng Plotly, tạo period dropdown
|   |   |-- report_generator.py      # Ghép HTML đầy đủ (Sankey + Income Statement + Coverage)
|   |   `-- index_generator.py       # Tạo dashboard index.html
|   |
|   |-- cache/
|   |   `-- cache_manager.py         # Cache file JSON với TTL theo namespace
|   `-- llm/
|       `-- provider.py              # Wrapper OpenAI với retry, rate limiting
|
|-- data/
|   |-- cache/
|   |   |-- filings/                 # Danh sách filing SEC (JSON, TTL 30 ngày)
|   |   |-- llm/                     # Kết quả trich xuất LLM (JSON, TTL 90 ngày)
|   |   `-- yfinance/                # Dữ liệu Yahoo Finance (JSON, TTL 7 ngày)
|   `-- segments/                    # Dữ liệu tổng hợp theo mã (JSON tổng hợp cuối cùng)
|
`-- output/
    `-- sankey/
        |-- index.html               # Dashboard toàn bộ 98 công ty
        `-- {TICKER}_sankey.html     # Báo cáo từng công ty
```


## Quy trình xử lý chi tiết

### Bước 1 - Phân loại mã cổ phiếu

Khi khởi động, `ticker_loader.py` đọc file Excel và phân loại từng mã vào một trong ba nhóm:

- **US_SEC**: Công ty Mỹ nộp 10-K/10-Q lên SEC EDGAR. Đây là nhóm lớn nhất (~73 mã). Ví dụ: AAPL, NVDA, MSFT, JPM.
- **INTL_SEC**: Công ty nước ngoài nộp 20-F/6-K lên SEC EDGAR. Gồm 7 mã: TSM, ASML, TCEHY, RHHBY, HSBC, AZN, NVS.
- **INTL_YAHOO**: Công ty quốc tế không nộp SEC, chỉ dùng Yahoo Finance. Gồm 18 mã như Samsung (005930.KS), Toyota (TM), LVMH (MC.PA), Siemens (SIE.DE).

Ngoài ra mỗi mã còn được gắn nhãn ngành đặc thù để áp dụng logic trich xuất phù hợp:
- **financial**: JPM, BAC, GS, V, MA... Công ty tài chính có cấu trúc P&L khác biệt (không có COGS, dùng net interest income).
- **pharma**: LLY, ABBV, MRK, AZN... Công ty dược có phân khúc theo loại thuốc và khu vực.
- **conglomerate**: BRK-B, GE, SIE.DE... Tập đoàn đa ngành cần xử lý đặc biệt.
- **standard**: Phần còn lại, áp dụng logic P&L thông thường.

### Bước 2 - Kiểm tra cache

Trước khi làm bất cứ điều gì, hệ thống kiểm tra `data/segments/{TICKER}.json`. Nếu file tồn tại và có ít nhất 1 kỳ annual thì bỏ qua toàn bộ bước lấy dữ liệu, nhảy thẳng sang bước render HTML. Đây là lý do khi chạy lại `python main.py --all` lần thứ hai rất nhanh.

### Bước 3 - Lấy danh sách filing

Với US_SEC và INTL_SEC, hệ thống gọi SEC EDGAR API để lấy danh sách filing:

```
GET https://data.sec.gov/submissions/{CIK}.json
```

Mỗi filing được ghi lại thành một `FilingRecord` gồm: ticker, loại form (10-K/10-Q/20-F), kỳ báo cáo, ngày nộp, accession number, CIK.

Giới hạn tốc độ: 8 request/giây (SEC quy định tối đa 10 req/s, để biên an toàn). Tất cả HTTP request đều có retry với exponential backoff (tối đa 5 lần, delay cơ sở 2 giây).

Kết quả được cache 30 ngày trong `data/cache/filings/`.

### Bước 4 - Trich xuất P&L từ SEC filing

Với mỗi filing, `edgar_client.py` dùng thư viện `edgartools` để parse filing:

```python
obj = get_filing_obj(filing)   # TenK / TenQ / TwentyF object
pnl = pnl_from_filing_obj(obj) # Đọc income_statement có cấu trúc
```

`edgartools` đọc trực tiếp từ XBRL có cấu trúc trong filing, không cần LLM. Kết quả là dict chứa các chỉ số P&L:

| Trường | XBRL concept tương ứng |
|--------|------------------------|
| `total_revenue` | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` |
| `gross_profit` | `GrossProfit` |
| `operating_income` | `OperatingIncomeLoss` |
| `net_income` | `NetIncomeLoss` |
| `cogs` | `CostOfRevenue`, `CostOfGoodsSold` |
| `rd_expense` | `ResearchAndDevelopmentExpense` |
| `sga_expense` | `SellingGeneralAndAdministrativeExpense` |
| `interest_expense` | `InterestExpense` |
| `income_tax` | `IncomeTaxExpense` |

Đây là dữ liệu có cấu trúc, độ chính xác cao, không cần LLM.

### Bước 5 - Trich xuất phân khúc doanh thu

Đây là bước phức tạp nhất. Hệ thống thử XBRL trước, nếu không đủ thì dùng LLM.

#### 5a - Thử XBRL dimensions

```python
segments = segments_from_xbrl_dimensions(obj, total_revenue=total_rev)
```

Hàm này tìm kiếm các XBRL dimension trong filing theo trục `StatementBusinessSegmentsAxis`. Mỗi dimension value là một phân khúc kinh doanh với giá trị doanh thu tương ứng.

Sau đó tính độ phủ:

```
xbrl_coverage = sum(segment.value for segment in segments) / total_revenue
```

Nếu `xbrl_coverage >= 0.7` (XBRL giải thích được ít nhất 70% doanh thu), dùng kết quả XBRL luôn, đặt `method = "xbrl"`.

#### 5b - Fallback sang LLM nếu XBRL không đủ

Nếu XBRL trả về rỗng hoặc `xbrl_coverage < 0.7`, hệ thống lấy văn bản từ note "Segment Information" trong filing:

```python
note_text = get_segment_note_text(obj)  # Thường 5,000-30,000 ký tự
```

Văn bản này sau đó được cắt thông minh (smart slicing): lấy 30,000 ký tự trước anchor "Segment" và 90,000 ký tự sau, tổng không quá 120,000 ký tự (~30k tokens), vừa với context window của gpt-4o-mini.

LLM được gọi với structured output (JSON schema), yêu cầu trả về danh sách:

```json
[
  {"segment_name": "Cloud & AI", "value": 54800000000},
  {"segment_name": "Personal Computing", "value": 23500000000}
]
```

Nếu gpt-4o-mini trả về rỗng hoặc lỗi, tự động fallback sang gpt-4o.

Sau đó so sánh độ phủ giữa XBRL và LLM:

```python
llm_coverage  = sum(s.value for s in llm_segs) / total_rev
xbrl_coverage = sum(s.value for s in xbrl_segs) / total_rev

# Chọn kết quả có độ phủ cao hơn
if llm_coverage > xbrl_coverage:
    segments = llm_segs
    method = "edgar+llm"
else:
    segments = xbrl_segs  # giữ XBRL
```

Kết quả LLM được cache 90 ngày trong `data/cache/llm/`.

#### 5c - Kiểm tra tỉ lệ scale

Sau khi có segments, hệ thống kiểm tra xem tổng các phân khúc có hợp lý so với `total_revenue` không:

```python
seg_sum = sum(s.value for s in segments)
scale   = total_revenue / seg_sum

# Chỉ rescale nếu chênh lệch > 25%
if not (0.8 <= scale <= 1.25):
    for s in segments:
        s.value *= scale  # điều chỉnh tỉ lệ để tổng khớp total_revenue
```

Bước này xử lý trường hợp LLM trả về đơn vị sai (ví dụ: trich xuất số liệu tính bằng triệu thay vì đơn vị tuyệt đối).

#### 5d - Kiểm tra chéo với Yahoo Finance

Nếu có dữ liệu Yahoo Finance, hệ thống so sánh:

```python
diff = abs(sd.total_revenue - yahoo_rev) / max(abs(yahoo_rev), 1)
if diff > 0.15:
    sd.notes.append("Warning: chênh lệch >15% so với Yahoo Finance")
```

### Bước 6 - Chuẩn hóa tên phân khúc

Vấn đề thực tế: cùng một phân khúc kinh doanh nhưng tên gọi trong filing thay đổi qua các năm. Ví dụ: "Greater China" năm 2022, "China" năm 2023, "Mainland China, Hong Kong and Taiwan" năm 2024.

`normalizer.py` giải quyết bằng fuzzy matching với thư viện `rapidfuzz`:

```python
best_match, score, _ = process.extractOne(
    name, canonical_names, scorer=fuzz.token_sort_ratio
)
if score >= 80:  # ngưỡng 80% tương đồng
    canonical_map[name] = best_match  # gộp vào tên canonical
else:
    canonical_names.append(name)      # đây là phân khúc mới
    canonical_map[name] = name
```

Thuật toán `token_sort_ratio` sắp xếp các từ trước khi so sánh, nên "Cloud Computing Services" và "Services Cloud Computing" vẫn được coi là giống nhau.

Tên canonical được chọn theo nguyên tắc: ưu tiên tên dài hơn vì thường đầy đủ mô tả hơn (sort theo độ dài giảm dần trước khi xử lý).

Sau khi map, các phân khúc trùng tên trong cùng một kỳ được gộp lại bằng cách cộng giá trị.

Canonical map được lưu vào `data/segments/{TICKER}_canonical.json` để kiểm tra thủ công khi cần.

### Bước 7 - Tổng hợp đa kỳ

`segment_aggregator.py` nhận toàn bộ list `SegmentData` (gồm annual và quarterly) rồi tổng hợp:

```python
annuals    = sorted(annual_periods,    key=lambda s: s.period, reverse=True)
quarterlies = sorted(quarterly_periods, key=lambda s: s.period, reverse=True)

agg = AggregatedCompanyData(
    latest_annual     = annuals[0],      # kỳ annual gần nhất
    annual_periods    = annuals,          # tất cả kỳ annual (tối đa 3)
    quarterly_periods = quarterlies,      # tất cả kỳ quarterly (tối đa 12)
    annual_count      = len(annuals),
    quarterly_count   = len(quarterlies),
    has_partial_data  = len(annuals) < 3 or len(quarterlies) < 8,
)
```

Đồng thời xây dựng `segment_trend`: dict ánh xạ tên phân khúc sang chuỗi giá trị theo thời gian:

```python
segment_trend = {
    "iPhone": [
        {"period": "FY2023", "value": 200583000000, "is_annual": True},
        {"period": "FY2024", "value": 201183000000, "is_annual": True},
    ],
    "Services": [...]
}
```

Kết quả được lưu vào `data/segments/{TICKER}.json`.


## Tính toán chỉ số tài chính

### Điểm tin cậy (Confidence Score)

Mỗi `SegmentData` được tính điểm tin cậy từ 0.0 đến 1.0 dựa trên mức độ đầy đủ của dữ liệu:

```python
score = 0.0

# Doanh thu là chỉ số bắt buộc, chiếm 40%
if total_revenue is not None and total_revenue != 0:
    score += 0.40

# Các chỉ số P&L cơ bản, mỗi cái 10-15%
if gross_profit is not None:      score += 0.10
if operating_income is not None:  score += 0.15
if net_income is not None:        score += 0.15

# Các dòng chi phí chi tiết (COGS, R&D, SG&A, Interest, Tax)
# Mỗi cái +4%, tổng tối đa 20%
pnl_count = sum(1 for f in (cogs, rd_expense, sga_expense, interest_expense, income_tax)
                if f is not None)
score += min(pnl_count * 0.04, 0.20)

# Phân khúc: không có phân khúc bị giới hạn tối đa 55%
if len(segments) == 0:
    score = min(score, 0.55)
elif len(segments) >= 2:
    score = min(1.0, score + 0.30)  # có ít nhất 2 phân khúc: +30%
elif len(segments) == 1:
    score = min(1.0, score + 0.10)  # chỉ 1 phân khúc: +10%
```

Ví dụ: Apple với đầy đủ P&L và 5 phân khúc đạt 1.0 (100%). Công ty chỉ có doanh thu và net income từ Yahoo nhưng không có phân khúc đạt khoảng 0.55 (55%).

### Tính Trailing Twelve Months (TTM)

TTM được dùng để tính doanh thu 12 tháng gần nhất, không phụ thuộc vào kỳ năm tài chính:

```python
last4q = quarterly_periods[:4]  # 4 quý gần nhất

if len(last4q) >= 4:
    ttm_revenue          = sum(q.total_revenue    for q in last4q if q.total_revenue)
    ttm_operating_income = sum(q.operating_income for q in last4q if q.operating_income)
    ttm_net_income       = sum(q.net_income       for q in last4q if q.net_income)
else:
    # Fallback: dùng annual gần nhất thay thế
    ttm_revenue          = annual_periods[0].total_revenue
    ttm_operating_income = annual_periods[0].operating_income
    ttm_net_income       = annual_periods[0].net_income
```

### Các chỉ số biên lợi nhuận

Tính toán trực tiếp khi render HTML, không lưu trong JSON:

```python
gross_margin     = gross_profit     / total_revenue  # Biên lợi nhuận gộp
operating_margin = operating_income / total_revenue  # Biên lợi nhuận hoạt động
net_margin       = net_income       / total_revenue  # Biên lợi nhuận ròng
```

### Tính YoY (Year-over-Year)

```python
yoy_pct = (current_value - prior_value) / abs(prior_value) * 100
```

Dùng `abs(prior_value)` ở mẫu số để xử lý đúng trường hợp năm trước lỗ (giá trị âm). Nếu một trong hai giá trị là None hoặc prior = 0, hiển thị "—" thay vì tính.

### Lựa chọn phân khúc tối ưu cho Sankey

Khi một công ty có nhiều tập phân khúc chồng lên nhau (ví dụ Apple có cả phân khúc địa lý lẫn phân khúc sản phẩm), hệ thống chọn tập nào hợp lý hơn:

```python
seg_sum = sum(s.value for s in segments)

# Nếu tổng <= 110% doanh thu, không có chồng chéo, dùng luôn
if seg_sum <= total_rev * 1.10:
    return segments

# Tách địa lý vs phi địa lý
geo_segs     = [s for s in segments if any(k in s.segment_name.lower()
               for k in {"americas", "europe", "china", "japan", "asia", "emea", ...})]
non_geo_segs = [s for s in segments if s not in geo_segs]

# Ưu tiên phi địa lý (phân khúc sản phẩm/dịch vụ) nếu tổng trong khoảng 70-130% doanh thu
non_geo_sum = sum(s.value for s in non_geo_segs)
if 0.70 * total_rev <= non_geo_sum <= 1.30 * total_rev:
    return non_geo_segs

# Fallback: dùng địa lý nếu phù hợp
geo_sum = sum(s.value for s in geo_segs)
if 0.70 * total_rev <= geo_sum <= 1.30 * total_rev:
    return geo_segs
```

Đối với Apple (AAPL), do dữ liệu XBRL chỉ có phân khúc địa lý, hệ thống cho phép override thủ công qua `PRODUCT_SEGMENT_OVERRIDES` trong `config.py`. Khi Sankey được build, nếu có override cho ticker + kỳ tương ứng, dữ liệu override được dùng thay cho dữ liệu từ JSON:

```python
# FY2024: override bằng phân khúc sản phẩm từ annual report
PRODUCT_SEGMENT_OVERRIDES = {
    "AAPL": {
        "FY2024": [
            {"segment_name": "iPhone",                        "value": 201183000000.0},
            {"segment_name": "Services",                      "value":  96169000000.0},
            {"segment_name": "Wearables, Home & Accessories", "value":  37005000000.0},
            {"segment_name": "Mac",                           "value":  29984000000.0},
            {"segment_name": "iPad",                          "value":  26694000000.0},
        ],
        "FY2023": [...]
    }
}
```


## Xây dựng biểu đồ Sankey

Sankey chart thể hiện dòng chảy tài chính từ doanh thu đến lợi nhuận ròng qua 7 lớp:

```
Lớp 0: Phân khúc doanh thu  --\
                               +--> Lớp 1: Tổng doanh thu --+--> Lớp 2a: Lợi nhuận gộp
                                                             |         \--> Lớp 3: R&D, SG&A
                                                             |              \--> Lớp 4: EBIT
                                                             |                   \--> Lớp 5: Thuế
                                                             |                        \--> Lớp 6: Lợi nhuận ròng
                                                             `--> Lớp 2b: Giá vốn hàng bán (thoát ra)
```

### Tính toán các node

**Gross Profit và COGS** (khi thiếu một trong hai):

```python
if gross_profit is not None and cogs is None:
    cogs = total_revenue - gross_profit

elif cogs is not None and gross_profit is None:
    gross_profit = total_revenue - cogs

elif gross_profit is None and cogs is None:
    # Ước tính từ operating income và opex
    opex = (rd_expense or 0) + (sga_expense or 0)
    gross_profit = operating_income + opex
    cogs = total_revenue - gross_profit
```

**Other Operating Expenses** (chi phí vận hành còn lại):

```python
other_opex = gross_profit - rd_expense - sga_expense - operating_income
# Chỉ hiển thị nếu > 1% doanh thu (để tránh nhiễu)
if other_opex > total_revenue * 0.01:
    add_node("Other OpEx", other_opex)
```

**Đảm bảo link nhỏ vẫn nhìn thấy được**:

```python
min_val = max(total_revenue * 0.01, abs(val) * 0.001)
link_val = max(abs(val), min_val)  # Không cho link nào nhỏ hơn 1% tổng doanh thu
```

**Vị trí node** (trục x từ 0.0 đến 1.0):

```
Phân khúc (x=0.0) -> Tổng DT (x=0.2) -> Lợi nhuận gộp (x=0.4) -> R&D/SG&A (x=0.6)
-> EBIT (x=0.75) -> Thuế/Lãi vay (x=0.87) -> Lợi nhuận ròng (x=1.0)
```

Vị trí trục y của các phân khúc được phân bố đều:

```python
for i, seg in enumerate(visible_segments):
    y_pos = (i + 0.5) / n_display  # Phân bố đều từ 0 đến 1
```

**Phân khúc nhỏ** (< 1.5% doanh thu) được gộp vào node "Other":

```python
MIN_PCT  = 0.015  # 1.5% ngưỡng tối thiểu để hiển thị riêng
MAX_SEGS = 8      # Tối đa 8 phân khúc hiển thị

for seg in sorted_segments:
    if seg.value / total_revenue < MIN_PCT or len(visible) >= MAX_SEGS:
        other_val += seg.value
    else:
        visible.append(seg)
```

### Màu sắc nodes và links

```python
SANKEY_COLORS = {
    "segment":          "#1f77b4",  # Xanh dương: phân khúc doanh thu
    "total_revenue":    "#2ca02c",  # Xanh lá: doanh thu tổng
    "gross_profit":     "#2ca02c",  # Xanh lá: lợi nhuận gộp
    "cogs":             "#d62728",  # Đỏ: giá vốn (chi phí, thoát ra)
    "rd":               "#ff7f0e",  # Cam: R&D
    "sga":              "#ff7f0e",  # Cam: SG&A
    "other_opex":       "#ffbb78",  # Cam nhạt: chi phí khác
    "operating_income": "#2ca02c",  # Xanh lá: EBIT
    "interest":         "#7f7f7f",  # Xám: lãi vay
    "tax":              "#7f7f7f",  # Xám: thuế
    "net_income":       "#2ca02c",  # Xanh lá: lợi nhuận ròng
    "net_loss":         "#d62728",  # Đỏ: lỗ ròng
}
```

Links dùng màu semi-transparent (alpha 0.4) từ màu của node nguồn:

```python
link_color = f"rgba({r},{g},{b},0.4)"  # Chuyển hex sang rgba với alpha 40%
```

### Period dropdown

Mỗi kỳ tài chính được build thành một `SankeyData` object độc lập. Khi render HTML, tất cả dữ liệu được nhúng dưới dạng JSON vào `<script>`:

```javascript
var SANKEY_DATA = {
    "FY2025": { nodes: [...], links: [...] },
    "FY2024": { nodes: [...], links: [...] },
    "FY2023": { nodes: [...], links: [...] },
};
```

Khi người dùng đổi dropdown, JavaScript cập nhật biểu đồ Plotly bằng `Plotly.react()` với dữ liệu tương ứng, không cần reload trang.


## Bảng Income Statement và YoY

### Chế độ Annual

Lấy 2 kỳ annual gần nhất từ `agg.annual_periods` (đã sắp xếp giảm dần):

```python
annual = sorted(agg.annual_periods, key=lambda p: p.period, reverse=True)
curr_period = annual[0]  # Ví dụ: FY2025
prev_period = annual[1]  # Ví dụ: FY2024
```

Bảng hiển thị 4 cột: Metric | FY2025 | FY2024 | YoY%

### Chế độ Quarterly

Hệ thống ghép từng quý năm nay với quý cùng kỳ năm trước:

```python
# Tên kỳ có dạng "2025Q4", "2024Q4", "2025Q2"...
for qp in sorted(qtr_map.keys(), reverse=True):
    year_str, qnum = qp.split("Q")      # "2025", "4"
    prior_key = f"{int(year_str)-1}Q{qnum}"  # "2024Q4"

    if prior_key in qtr_map:
        qtr_pairs.append((qtr_map[qp], qtr_map[prior_key]))
```

Kết quả là tối đa 4 cặp quý gần nhất (2025Q4 vs 2024Q4, 2025Q2 vs 2024Q2, ...). Mỗi cặp hiển thị dưới dạng tab riêng.

### Phân loại dòng trong bảng

```python
METRICS = [
    ("Revenue",          "total_revenue",    False, "divider"),   # In đậm, nền xám nhạt
    ("Cost of Revenue",  "cogs",             False, "normal"),
    ("Gross Profit",     "gross_profit",     False, "divider"),
    ("Gross Margin",     None,               True,  "margin"),    # In nghiêng, là tỉ lệ %
    ("R&D Expense",      "rd_expense",       False, "normal"),
    ("SG&A Expense",     "sga_expense",      False, "normal"),
    ("Operating Income", "operating_income", False, "divider"),
    ("Op. Margin",       None,               True,  "margin"),
    ("Net Income",       "net_income",       False, "divider"),
    ("Net Margin",       None,               True,  "margin"),
]
```

Các dòng `margin` được tính là `value / total_revenue`, không lưu trực tiếp trong JSON mà tính lại mỗi lần render.

YoY hiển thị màu xanh nếu tăng trưởng dương, màu đỏ nếu âm. Riêng các dòng chi phí (Cost of Revenue, R&D, SG&A) nếu tăng cũng hiển thị màu đỏ vì đó là chi phí tăng không tốt. Hiện tại code render đồng nhất: xanh là tăng, đỏ là giảm cho mọi dòng. Nếu muốn đảo logic màu cho dòng chi phí, cần chỉnh `_yoy_cell()` trong `report_generator.py`.


## Hệ thống cache

Cache lưu trong `data/cache/` dưới dạng file JSON với timestamp. Mỗi entry có dạng:

```json
{
  "timestamp": 1714320000.0,
  "data": { ... }
}
```

Khi đọc, hệ thống kiểm tra:

```python
age_days = (time.time() - entry["timestamp"]) / 86400
if age_days > TTL[namespace]:
    delete(cache_file)
    return None  # cache miss, cần lấy lại
```

TTL theo namespace:

| Namespace | TTL | Lý do |
|-----------|-----|-------|
| filings | 30 ngày | Danh sách filing ít thay đổi |
| yfinance | 7 ngày | Dữ liệu Yahoo cập nhật hàng tuần |
| xbrl | 7 ngày | Dữ liệu XBRL ổn định nhưng có thể có sửa đổi nhỏ |
| llm | 90 ngày | Trich xuất LLM tốn tiền, ít thay đổi theo thời gian |
| segments | 30 ngày | Dữ liệu tổng hợp cuối cùng |

Tên file cache được tạo từ namespace + key, với `/`, `\`, `:` được thay bằng `_`. Ví dụ:

```
filings/filings_list_AAPL_10-K_10-Q.json
llm/seg_AAPL_FY2025_standard_openai_30b3603f.json
yfinance/AAPL.json
```

Hash ở cuối tên file LLM là MD5 của nội dung prompt, đảm bảo khi thay đổi prompt thì cache tự động bị bỏ qua.


## Cấu hình nâng cao

Các hằng số quan trọng trong `config.py`:

```python
# Phạm vi dữ liệu lấy
YEARS_BACK    = 3   # 3 năm tài chính
QUARTERS_BACK = 12  # 12 quý

# Giới hạn tốc độ SEC EDGAR
SEC_RATE_LIMIT     = 8    # request/giây
SEC_RETRY_MAX      = 5    # số lần retry tối đa
SEC_RETRY_BASE_SEC = 2.0  # delay cơ sở (giây), tăng theo exponential backoff

# Xử lý song song
MAX_TICKER_WORKERS = 3   # ticker song song (giữ thấp để tôn trọng SEC rate limit)
MAX_LLM_WORKERS    = 20  # LLM call song song trong một ticker

# XBRL
XBRL_CONFIDENCE_HIGH   = 0.75  # XBRL >= 75% -> chỉ dùng XBRL
XBRL_CONFIDENCE_MEDIUM = 0.30  # XBRL 30-75% -> dùng XBRL làm context cho LLM

# LLM
FILING_TEXT_MAX_CHARS = 120_000  # ~30k tokens, an toàn với 128k context window
SMART_SLICE_PRE       = 30_000   # ký tự trước anchor "Segment"
SMART_SLICE_POST      = 90_000   # ký tự sau anchor
```

Thêm phân khúc sản phẩm cho công ty khác trong `PRODUCT_SEGMENT_OVERRIDES`:

```python
PRODUCT_SEGMENT_OVERRIDES["MSFT"] = {
    "FY2024": [
        {"segment_name": "Intelligent Cloud",                  "value": 105396000000.0},
        {"segment_name": "Productivity and Business Processes","value":  78999000000.0},
        {"segment_name": "More Personal Computing",            "value":  59655000000.0},
    ]
}
```


## Lưu ý vận hành

- Không tăng `MAX_TICKER_WORKERS` quá 5. SEC EDGAR giới hạn 10 req/s tổng cộng cho tất cả thread, vượt quá sẽ bị HTTP 429 và tạm khóa IP.
- Lần đầu chạy `--all` mất khoảng 20-40 phút tùy tốc độ mạng và số lần cần gọi LLM. Các lần sau chỉ mất vài phút vì dùng cache.
- Một số công ty quốc tế (Samsung, Sony, Toyota) chỉ có dữ liệu tài chính tổng hợp từ Yahoo Finance, không có phân khúc chi tiết do không nộp SEC. Độ tin cậy thường dưới 60%.
- Apple (AAPL) có phân khúc sản phẩm cho FY2023 và FY2024. FY2025 hiển thị phân khúc địa lý vì dữ liệu FY2025 được thu thập sau thời điểm cập nhật cuối cùng của `PRODUCT_SEGMENT_OVERRIDES`.
- Để xóa cache và xử lý lại từ đầu cho một mã: `python main.py --tickers AAPL --no-cache`.
- Để chỉ cập nhật lại file HTML (không gọi API): `python main.py --all` (không có `--no-cache`).
