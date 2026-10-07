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
    # Desktop must not be dropped by grounding filter
    assert any(
        (it.get("description") or "").strip().lower() == "desktop"
        for it in items
    )
    packages = {it.get("stock_number") for it in items}
    assert packages >= {"PKG1", "PKG2", "PKG3", "PKG4"}
    # Header total is larger than listed packages — warn, don't invent lines
    warn = result.get("parse_warning") or ""
    assert "34251.46" in warn or "invoice total" in warn.lower()
