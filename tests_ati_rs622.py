"""ATI RS622: glued HS+UoM (81–82), part-code descriptions, Line Weight net sum."""
from pathlib import Path

from line_item_parser import LineItemParser
from parse_coverage import _carriage_charges_sum, coverage_warnings, items_total

RS622 = Path(
    '/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/'
    'attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/RS622.pdf'
)


def _f(x) -> float:
    try:
        return float(str(x or '').replace(',', ''))
    except (TypeError, ValueError):
        return 0.0


def test_glued_hs_uom_regex():
    import re
    m = re.match(r'^(\d{8})(?:\s+(.+))?$', '40169300 1 MREEL OF 10')
    assert m and m.group(1) == '40169300'
    assert m.group(2) == '1 MREEL OF 10'
    assert LineItemParser._ATI_UOM_RE.match('1 MREEL OF 10')


def test_rs622_pdf_if_pymupdf_available():
    try:
        import fitz
    except ImportError:
        return
    if not RS622.exists():
        return

    doc = fitz.open(str(RS622))
    parser = LineItemParser()
    raw = parser._parse_ati_format(doc, 'export', {})
    text = '\n'.join(p.get_text() for p in doc)
    items = parser._postprocess_items(raw, text)
    by = {str(it['line_number']): it for it in items}

    assert len(items) == 91, f'expected 91 lines, got {len(items)}'
    assert '10' in by and by['10']['total_value'] == '32.00'
    assert _f(by['10']['net_weight']) == 0.35

    assert by['81']['commodity_code'].startswith('40169300')
    assert 'MREEL' in (by['81'].get('uom') or '').upper()
    assert abs(_f(by['81']['net_weight']) - 0.77) < 0.01
    assert by['82']['commodity_code'].startswith('40169300')
    assert abs(_f(by['82']['net_weight']) - 1.31) < 0.01

    net_sum = round(sum(_f(it.get('net_weight')) for it in items), 2)
    assert abs(net_sum - 149.37) <= 0.05, f'net weight sum {net_sum} != 149.37'

    money = items_total(items)
    assert abs(money - 7769.81) < 0.05, f'goods sum {money} != 7769.81'
    assert abs((_carriage_charges_sum(text) or 0) - 284.04) < 0.01

    _, _, warnings = coverage_warnings(items, text)
    assert not any('do not match invoice total' in w for w in warnings), warnings


if __name__ == '__main__':
    test_glued_hs_uom_regex()
    test_rs622_pdf_if_pymupdf_available()
    print('ok')
