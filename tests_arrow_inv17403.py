"""Arrow INV00017403: cross-page (cc:), ignore MAWB digits, packing freight."""
from pathlib import Path

from line_item_parser import LineItemParser
from parse_coverage import (
    _carriage_charges_sum,
    coverage_warnings,
    document_commodity_codes,
    invoice_total_hint,
    is_commodity_hs,
    items_total,
)

PDF = Path(
    '/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/'
    'attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/Invoice_INV00017403.pdf'
)


def test_mawb_not_commodity_hs():
    assert not is_commodity_hs('77355633')


def test_inv17403_pdf_if_available():
    try:
        import fitz
    except ImportError:
        return
    if not PDF.exists():
        return

    doc = fitz.open(str(PDF))
    text = '\n'.join(p.get_text() for p in doc)
    assert '77355633' not in document_commodity_codes(text)
    assert abs((_carriage_charges_sum(text) or 0) - 368.0) < 0.01
    assert abs((invoice_total_hint(text) or 0) - 1240.51) < 0.01

    parser = LineItemParser()
    items = parser._postprocess_items(
        parser._parse_arrow_export_format(text.split('\n'), 'export', {}),
        text,
    )
    by = {str(it['line_number']): it for it in items}
    assert len(items) == 41
    assert by['37']['commodity_code'].startswith('96099090')
    assert 'crayon' in by['37']['description'].lower()
    assert 'gift for boys' in by['37']['description'].lower()
    assert 'we hereby' not in by['37']['description'].lower()
    assert '235-77355633' not in by['37']['description']
    # Item 19 description continues onto page 2 with size + (cc:)
    assert by['19']['commodity_code'].startswith('63049200')
    assert '50x50cm' in by['19']['description']
    assert 'uk origin' not in by['19']['description'].lower()
    assert by['38']['commodity_code'].startswith('39191019')
    assert abs(items_total(items) - 872.51) < 0.05
    _, _, warnings = coverage_warnings(items, text)
    assert not warnings, warnings


if __name__ == '__main__':
    test_mawb_not_commodity_hs()
    test_inv17403_pdf_if_available()
    print('ok')
