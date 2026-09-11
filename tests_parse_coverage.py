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


def test_invoice_number_ocr_ne_and_title():
    from parse_coverage import extract_invoice_number
    blob = """
    Commercial Invoice
    Page 1 of 4
    Phone: +44 (0) 1323 444444 Fax: +44 (0) 1323 444455 Invoice Ne: SIN134283
    Invoice Date: 07 September 2026
    VAT No: GB 377109145
    """
    assert extract_invoice_number(blob) == 'SIN134283'
    assert extract_invoice_number('Invoice No: INV-2026-001\nDate: 2026-04-20') == 'INV-2026-001'
    assert extract_invoice_number('Invoice number\n2221875953') == '2221875953'


def test_invoice_number_skips_consignee_invoice_to():
    from parse_coverage import extract_invoice_number
    blob = """
    Commercial Invoice
    Consignee / Invoice To:
    ACME IMPORTS LTD
    22 Bolt Avenue
    Invoice No: AE88421
    Invoice Date: 07 September 2026
    """
    assert extract_invoice_number(blob) == 'AE88421'
    assert extract_invoice_number('Consignee / Invoice To:\nACME LTD') != 'CONSIGNEE'
    assert extract_invoice_number('Consignee / Invoice To:\nACME LTD') is None


def test_invoice_number_standard_no_on_same_row_as_invoice_to():
    """PDF two-column headers glue 'Invoice To' and 'Invoice No' onto one line."""
    from parse_coverage import extract_invoice_number
    arrow = (
        "Consignee / Invoice To: ARROW CUSTOMER LTD "
        "Invoice No: INV00017249 Invoice Date: 11/09/2026"
    )
    assert extract_invoice_number(arrow) == 'INV00017249'
    assert extract_invoice_number('Invoice To: ACME LTD\nInvoice No: INV00017252') == 'INV00017252'
    assert extract_invoice_number('Invoice\nNo:\nINV00017249') == 'INV00017249'
    assert extract_invoice_number('Invoice No: 88421') == '88421'
    assert extract_invoice_number('Invoice No: S81217') == 'S81217'
    assert extract_invoice_number('Invoice No:') is None
    assert extract_invoice_number('Invoice To: CONSIGNEE LTD') is None
    assert extract_invoice_number('Invoice Date: 11/09/2026') is None
    assert extract_invoice_number('Invoice No: INV-2026-001') == 'INV-2026-001'


def test_invoice_number_sage_value_after_header_labels():
    from parse_coverage import extract_invoice_number
    sage = """
    Invoice No. Tax Point Page
    INV00017249 11/09/2026 1 of 1
    """
    assert extract_invoice_number(sage) == 'INV00017249'
    assert extract_invoice_number('INV00017249\nInvoice No:') == 'INV00017249'
    assert extract_invoice_number('Invoice No: INV 00017249') == 'INV00017249'
    padded = ('Address line\n' * 80) + 'INVOICE NO. IPGB025993\n'
    assert extract_invoice_number(padded) == 'IPGB025993'
    assert extract_invoice_number('Invoice No: SO16') is None


def test_invoice_number_not_account_or_po():
    from parse_coverage import extract_invoice_number
    assert extract_invoice_number(
        'Invoice No: INV00017249 Your Ref: PO-2026-001 Account No: 33404927'
    ) == 'INV00017249'
    assert extract_invoice_number(
        'Invoice No: 88421 Your Ref: PO-99'
    ) == '88421'
    assert extract_invoice_number(
        'Invoice No. Account Your Ref\nINV00017249 33404927 PO-99'
    ) == 'INV00017249'
    assert extract_invoice_number(
        'Account No.: 33404927\nInvoice No: SIN134283'
    ) == 'SIN134283'
    assert extract_invoice_number(
        'Invoice No: AE88421 Order No: PO12345'
    ) == 'AE88421'
    assert extract_invoice_number('Invoice No: 88421') == '88421'
    assert extract_invoice_number('Invoice number\n2221875953') == '2221875953'
    assert extract_invoice_number('Your Ref: PO-2026-001\nInvoice No:') is None


def test_invoice_number_from_pdf_words_right_of_label():
    from parse_coverage import invoice_number_from_pdf_words
    # Invoice No: INV00017249     Your Ref: PO-2026-001
    words = [
        (10, 20, 50, 32, 'Invoice', 0, 0, 0),
        (52, 20, 70, 32, 'No:', 0, 0, 1),
        (90, 20, 170, 32, 'INV00017249', 0, 0, 2),
        (200, 20, 240, 32, 'Your', 0, 0, 3),
        (242, 20, 270, 32, 'Ref:', 0, 0, 4),
        (280, 20, 360, 32, 'PO-2026-001', 0, 0, 5),
    ]
    assert invoice_number_from_pdf_words(words) == 'INV00017249'



def test_export_cpc_defaults_to_1040():
    from parse_coverage import extract_cpc_code, default_cpc
    assert default_cpc('export') == '1040'
    assert default_cpc('import') == '4000'
    assert extract_cpc_code('', 'export') == '1040'
    assert extract_cpc_code('CPC: 1000001 Perm Export / LIC99', 'export') == '1040'
    assert extract_cpc_code('CPC: 1040', 'export') == '1040'
    assert extract_cpc_code('Something CPC: 4000', 'export') == '1040'


def test_wrap_row_without_hs_merges():
    from parse_coverage import is_table_overflow_line, merge_overflow_items
    assert is_table_overflow_line('LENGTH: 200mm BREAKLOAD: 1850kg', previous_has_hs=True)
    assert not is_table_overflow_line(
        'WSS210 SOFT SHACKLE 4mm 2 EA 5607491100 GB 14.53',
        previous_has_hs=True,
    )
    items = [
        {
            'stock_number': 'WSS210',
            'description': 'SOFT SHACKLE 4mm D12 BLK - PK OF 2',
            'quantity': '2',
            'total_value': '14.53',
            'commodity_code': '56074911',
        },
        {
            'stock_number': '',
            'description': 'LENGTH: 200mm BREAKLOAD: 1850kg',
            'quantity': '',
            'total_value': '',
            'commodity_code': '',
        },
    ]
    merged = merge_overflow_items(items)
    assert len(merged) == 1
    assert 'LENGTH: 200mm' in merged[0]['description']


if __name__ == '__main__':
    test_ignores_bank_account_as_hs()
    test_harvest_recovers_missed_hs()
    test_invoice_total_and_gap_warning()
    test_not_commodity()
    test_invoice_number_ocr_ne_and_title()
    test_invoice_number_skips_consignee_invoice_to()
    test_invoice_number_standard_no_on_same_row_as_invoice_to()
    test_invoice_number_sage_value_after_header_labels()
    test_invoice_number_not_account_or_po()
    test_invoice_number_from_pdf_words_right_of_label()
    test_export_cpc_defaults_to_1040()
    test_wrap_row_without_hs_merges()
    print('ok')
