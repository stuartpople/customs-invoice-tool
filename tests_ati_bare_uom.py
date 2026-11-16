"""ATI invoices: bare UoM like 'Box' must not drop the line (ATI 614 item 36)."""
from line_item_parser import LineItemParser
from parse_coverage import _carriage_charges_sum, coverage_warnings, items_total


def test_ati_bare_box_uom_is_accepted():
    assert LineItemParser._ATI_UOM_RE.match('1 OF 1')
    assert LineItemParser._ATI_UOM_RE.match('1 BOX OF 12')
    assert 'BOX' in LineItemParser._ATI_BARE_UOM
    # Simulate the consume step used in _parse_ati_format
    lines = ['36', 'CN', '39191000', 'Box', '40', '84.70', '3388.00', '1.00', '40.00']
    i = 3
    uom_tok = lines[i]
    first = uom_tok.split()[0].upper()
    assert LineItemParser._ATI_UOM_RE.match(uom_tok) or first in LineItemParser._ATI_BARE_UOM
    i += 1
    nums = []
    while i < len(lines) and len(nums) < 5:
        cleaned = lines[i].replace(',', '')
        import re
        if re.match(r'^\d+\.?\d*$', cleaned):
            nums.append(cleaned)
            i += 1
        else:
            break
    assert nums[:3] == ['40', '84.70', '3388.00']


def test_packing_and_handling_counts_as_carriage():
    text = """
    Total Value                  13,734.56
    Packing & Handling Charges      337.27
    Invoice Total        GBP     14,071.83
    """
    assert abs((_carriage_charges_sum(text) or 0) - 337.27) < 0.01
    items = [{'total_value': '13734.56', 'commodity_code': '39191000'}]
    _, _, warnings = coverage_warnings(items, text)
    assert not any('do not match invoice total' in w for w in warnings), warnings


def test_ati614_pdf_if_pymupdf_available():
    try:
        import fitz
    except ImportError:
        return
    from pathlib import Path
    pdf = Path(
        '/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/'
        'attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/ATI_614.pdf'
    )
    if not pdf.exists():
        return
    doc = fitz.open(pdf)
    items = LineItemParser()._parse_ati_format(doc, 'export', {})
    assert len(items) == 40
    by = {str(it['line_number']): it for it in items}
    assert by['36']['total_value'] == '3388.00'
    assert by['36']['uom'] == 'Box'
    assert abs(items_total(items) - 13734.56) < 0.05
    text = '\n'.join(p.get_text() for p in doc)
    _, _, warnings = coverage_warnings(items, text)
    assert not any('do not match invoice total' in w for w in warnings), warnings


if __name__ == '__main__':
    test_ati_bare_box_uom_is_accepted()
    test_packing_and_handling_counts_as_carriage()
    test_ati614_pdf_if_pymupdf_available()
    print('ok')
