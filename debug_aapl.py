import sys, re, edgar, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
edgar.set_identity(os.getenv("SEC_USER_AGENT", "test test@test.com"))

from src.ingestion.edgar_client import get_filing_obj, product_segments_from_mda
from src.extraction.models import FilingRecord

company = edgar.Company("AAPL")
raw_filings = list(company.get_filings(form="10-K"))[:3]

for rf in raw_filings:
    year = str(rf.period_of_report)[:4]
    rec = FilingRecord(ticker='AAPL', form_type='10-K', period=f'FY{year}',
        filing_date=str(rf.filing_date)[:10], accession_number=str(rf.accession_number),
        cik=str(company.cik).zfill(10), is_annual=True,
        fiscal_year=int(year), fiscal_quarter=None)
    obj = get_filing_obj(rec)
    segs = product_segments_from_mda(obj, period=rec.period, is_annual=True)
    if segs:
        print(f'{rec.period}: {[(s.segment_name, f"${s.value/1e9:.1f}B") for s in segs]}')
    else:
        print(f'{rec.period}: FAILED')