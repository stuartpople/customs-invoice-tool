"""Yacht / shipyard package-grouped commercial invoice parser."""
from pathlib import Path
import json
import tempfile

import fitz

from line_item_parser import LineItemParser
from parse_coverage import invoice_total_hint, items_total


JESTER = Path(
    "/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/"
    "attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/"
    "YSCI26075_Jester_Vilanova_Forth_shipment_Commercial_Invoice.pdf"
)


def test_detects_yacht_package_layout():
    if not JESTER.exists():
        return
    text = "\n".join(p.get_text("text") for p in fitz.open(JESTER))
    assert LineItemParser._is_yacht_package_invoice(text)
    assert invoice_total_hint(text) == 34251.46


def test_jester_extracts_all_printed_lines():
    """PDF lists packages 1–4 only (£18,170); header total £34k is incomplete."""
    if not JESTER.exists():
        return
    doc = fitz.open(JESTER)
    pages = [
        {"page_number": i + 1, "text": page.get_text("text"), "status": "success"}
        for i, page in enumerate(doc)
    ]
    doc.close()
    jobdir = Path(tempfile.mkdtemp(prefix="yacht_"))
    (jobdir / "pages.json").write_text(json.dumps({"pages": pages}))
    result = LineItemParser().parse_job_items("t", jobdir, direction="export")
    items = result.get("items") or []
    assert result.get("format_type") == "yacht_package"
    assert len(items) == 24
    assert abs(items_total(items) - 18170.0) < 0.05
    # All three £2,500 wardrobe kits + remaining 94039100 lines (not collapsed to £5,850)
    hs940 = [
        it for it in items
        if str(it.get("commodity_code") or "").startswith("940391")
    ]
    assert len(hs940) == 12
    assert abs(sum(float(it["total_value"]) for it in hs940) - 11350.0) < 0.05
    wardrobe_2500 = [
        it for it in hs940 if abs(float(it["total_value"]) - 2500.0) < 0.01
    ]
    assert len(wardrobe_2500) == 3
    # Desktop must not be dropped by grounding filter
    assert any(
        (it.get("description") or "").strip().lower() == "desktop"
        for it in items
    )
    packages = {str(it.get("stock_number") or "").split("-")[0] for it in items}
    assert packages >= {"PKG1", "PKG2", "PKG3", "PKG4"}
    # Header total is larger than listed packages — warn, don't invent lines
    warn = result.get("parse_warning") or ""
    assert "34251.46" in warn or "invoice total" in warn.lower()
    assert "18170" in warn or "package" in warn.lower()


def test_dedupe_keeps_same_hs_qty_value_different_descriptions():
    """Regression: PKG+qty+£ used to collapse distinct lines to £5,850 on 94039100."""
    parser = LineItemParser()
    items = [
        {
            "description": "Owners Wardrobe - Flat Packed - 5 oak veneered panels plus a ply back",
            "commodity_code": "94039100",
            "quantity": "6",
            "total_value": "2500.00",
            "stock_number": "PKG3",
            "item_number": "1",
        },
        {
            "description": "Owners Wardrobe - Flat Packed",
            "commodity_code": "94039100",
            "quantity": "6",
            "total_value": "2500.00",
            "stock_number": "PKG3",
            "item_number": "2",
        },
        {
            "description": "Explorer Room Wardrobe - Flat Packed",
            "commodity_code": "94039100",
            "quantity": "6",
            "total_value": "2500.00",
            "stock_number": "PKG3",
            "item_number": "3",
        },
        {
            "description": "Side Cheeks x2 - 220cm x 60cm x 5cm",
            "commodity_code": "94039100",
            "quantity": "2",
            "total_value": "500.00",
            "stock_number": "PKG2",
            "item_number": "4",
        },
        {
            "description": "Wardrobe Doors x2 - 190cm x 55cm x 10cm",
            "commodity_code": "94039100",
            "quantity": "2",
            "total_value": "500.00",
            "stock_number": "PKG2",
            "item_number": "5",
        },
    ]
    kept = parser._postprocess_items(items)
    assert len(kept) == 5
    assert abs(sum(float(it["total_value"]) for it in kept) - 8500.0) < 0.05
