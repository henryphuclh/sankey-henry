## Nhóm 1 — edgartools không parse được income statement (EDGAR 0%)

**Mã bị ảnh hưởng:** AVGO, V, MU, MA, AMD, COST, PG, CSCO

Tất cả đều có `method=EDGAR`, `confidence=0%`, revenue và net income trống. Quy trình xảy ra như sau:

```
pnl_from_filing_obj(obj)       → toàn None  (edgartools không nhận dạng được XBRL tags)
get_segment_note_text(obj)     → ""          (không tìm thấy note text)
LLM không được gọi             → vì note_text rỗng
SegmentData: rev=None, conf=0
```

**Nguyên nhân kỹ thuật cụ thể:**
- **V, MA (Visa, Mastercard):** Công ty dịch vụ thanh toán báo cáo "Net revenues" theo XBRL tag `RevenueFromContractWithCustomerExcludingAssessedTax` nhưng có thể dùng extension tag riêng không nằm trong danh sách `XBRL_REVENUE_TAGS` của hệ thống.
- **AMD, MU:** Bán dẫn có cấu trúc XBRL phức tạp sau các thương vụ mua lại (AMD mua Xilinx 2022, MU có fiscal year kết thúc tháng 8).
- **COST, PG, CSCO:** Khả năng cao do edgartools version mismatch với cách filing được đánh index trên SEC EDGAR.
- **AVGO:** Đã phân tích ở trên — VMware acquisition làm thay đổi hoàn toàn cấu trúc 10-K.

Đây là giới hạn của thư viện `edgartools` — nó hoạt động tốt với ~85% filing chuẩn, nhưng một số công ty dùng XBRL extension namespace riêng hoặc cấu trúc presentation linkbase không chuẩn. Giải pháp đầy đủ là bổ sung thêm các XBRL tag vào `XBRL_REVENUE_TAGS` hoặc thêm fallback gọi LLM ngay cả khi `note_text` ngắn.

---
## Nhóm 2 — Công ty tài chính thiếu Revenue (JPM, BRK-B op margin trống)

**Mã bị ảnh hưởng:** JPM (revenue "—"), BRK-B (op margin trống)

Ngân hàng và công ty bảo hiểm **không có khái niệm "doanh thu" theo GAAP thông thường**. JPMorgan không bán hàng hóa — họ báo cáo:

```
Net Interest Income     = $89.3B  (lãi cho vay - lãi huy động)
Non-Interest Revenue    = $70.6B  (phí dịch vụ, trading, advisory)
Provision for Credit Losses = -$10.6B  (dự phòng rủi ro tín dụng)
```

XBRL tag `Revenues` hoặc `RevenueFromContractWithCustomer` không tồn tại trong filing của JPM — họ dùng `InterestAndFeeIncomeLoansAndLeases`, `NoninterestIncome`, v.v. Hệ thống hiện tại chỉ tìm các tag doanh thu chuẩn nên bỏ sót.

**Minh chứng:** Net income JPM hiển thị đúng `$57.05B` vì `NetIncomeLoss` là tag chuẩn không thay đổi giữa các ngành.

---

## Nhóm 3 — Công ty quốc tế chỉ có Yahoo Finance (55%)

**Mã bị ảnh hưởng:** 005930.KS (Samsung), TCEHY (Tencent)

Yahoo Finance chỉ cung cấp **tổng hợp tài chính** (aggregate income statement), không có phân khúc kinh doanh chi tiết. Samsung chia revenue thành Device Solutions, MX, Harman, v.v. nhưng Yahoo không expose dữ liệu này qua API. Confidence 55% phản ánh đúng thực tế: có đủ P&L tổng hợp nhưng thiếu segment breakdown.

```
Yahoo Finance API trả về:
  total_revenue: ✓
  gross_profit:  ✓
  net_income:    ✓
  segments:      ✗  (không có)
→ confidence = 0.55 (P&L đủ nhưng không có phân khúc)
```

---

## Nhóm 4 — Dữ liệu đặc thù (TSM 59%, LLY 77%)

- **TSM (4A/175Q, 59%):** XBRL parse được nhưng Taiwan Semiconductor báo cáo segment theo nhiều dimension (địa lý × ứng dụng × công nghệ), tổng các segment chỉ cover 59% doanh thu vì phần còn lại không được map vào đúng axis. Con số 175Q là bất thường, có thể do edgartools đang đếm nhầm 6-K filings liên tục.
- **LLY (77%, 1 segment):** Eli Lilly có phân khúc theo sản phẩm (Mounjaro, Trulicity, Jardiance...) nhưng LLM chỉ trich xuất được 1 phân khúc aggregate do cấu trúc note text phức tạp của pharma filing.

---

## Tóm tắt

| Nguyên nhân                               | Mã ảnh hưởng                         |
| ----------------------------------------- | ------------------------------------ |
| edgartools không nhận XBRL tag phi chuẩn  | AVGO, V, MU, MA, AMD, COST, PG, CSCO |
| Ngành tài chính có cấu trúc P&L khác biệt | JPM, BRK-B (partial)                 |
| Công ty quốc tế không có SEC filing       | Samsung, Tencent...                  |
| Phân khúc phức tạp, LLM cover không đủ    | LLY, TSM, NFLX...                    |
| **Trích xuất thành công đầy đủ**          | NVDA, AAPL, MSFT, AMZN...            |
