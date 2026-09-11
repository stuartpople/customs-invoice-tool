"""Coverage gate: a dedicated parser must not keep HS codes that are still in the text."""
from parse_coverage import (
    coverage_warnings,
    document_commodity_codes,
    harvest_hs_rows,
    invoice_total_hint,
    is_commodity_hs,
)


MARLOW_OCR = """
Marlow Ropes Ltd  www.marlowropes.com
Item No. Description Qty UoM HSCode Country Total Unit Price Total Amount
DR5125 D2 RACING 78 12mm GREEN/GREY 200mR 1 EA 5607491100 GB 21.22 712.08 712.08
TAE002 WHIPPING TWINE No4 RED 12SP 1 EA 5607501190 GB 0.12 26.00 26.00
FAB112 MARLOW TAPE CARDED 2 EA 3919101200 GB 0.17 2.71 5.42
FAFOOO SPLICING KIT 3 EA 7319909000 GB 0.21 36.17 108.51
Account No.: 33404927
Tax Exclusive Value 5,192.58
Total GBP Incl. VAT 5,192.58
"""


def test_ignores_bank_account_as_hs():
    codes = document_commodity_codes(MARLOW_OCR)
    assert '33404927' not in codes
    assert codes == {'56074911', '56075011', '39191012', '73199090'}


def test_harvest_recovers_missed_hs():
    items = [{
        'stock_number': 'DR5125',
        'description': 'D2 RACING',
        'quantity': '1',
        'total_value': '712.08',
        'commodity_code': '56074911',
    }]
    doc, parsed, _warnings = coverage_warnings(items, MARLOW_OCR)
    assert '56075011' in (doc - parsed)
    harvested = harvest_hs_rows(
        MARLOW_OCR.strip().split('\n'),
        pad_hs=lambda c: c[:8],
        page_at=lambda _i: 1,
        existing_keys={('DR5125', '56074911', '1', '712.08')},
        currency='GBP',
    )
    got = {(it['stock_number'], it['commodity_code'][:8]) for it in harvested}
    assert ('TAE002', '56075011') in got
    assert ('FAB112', '39191012') in got
    assert ('FAFOOO', '73199090') in got
    merged = items + harvested
    _, _, warnings2 = coverage_warnings(merged, MARLOW_OCR)
    missing_hs = [w for w in warnings2 if 'not parsed' in w]
    assert not missing_hs, warnings2


def test_invoice_total_and_gap_warning():
    assert invoice_total_hint(MARLOW_OCR) == 5192.58
    items = [{
        'stock_number': 'DR5125',
        'description': 'D2 RACING',
        'quantity': '1',
        'total_value': '712.08',
        'commodity_code': '56074911',
    }]
    _, _, warnings = coverage_warnings(items, MARLOW_OCR)
    assert any('do not match invoice total' in w for w in warnings)


def test_not_commodity():
    assert not is_commodity_hs('377109145')
    assert not is_commodity_hs('1323444444')
    assert is_commodity_hs('56074911')


if __name__ == '__main__':
    test_ignores_bank_account_as_hs()
    test_harvest_recovers_missed_hs()
    test_invoice_total_and_gap_warning()
    test_not_commodity()
    print('ok')
