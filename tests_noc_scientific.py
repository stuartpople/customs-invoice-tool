"""NOC / scientific packing-list parser (Laura Bassi + ACE scientist kits)."""
from pathlib import Path
import json
import tempfile

import fitz

from countries import looks_like_country_token
from line_item_parser import LineItemParser
from parse_coverage import cn8, document_commodity_codes


LAURA = Path(
    "/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/"
    "attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/"
    "280005867-RV_LAURA_BASSI_c_o-20260921.pdf"
)
ACE = Path(
    "/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/"
    "attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/"
    "Scientist_Invoice_ACE_FINAL_.pdf"
)


def _parse_pdf(path: Path):
    doc = fitz.open(path)
    pages = [
        {"page_number": i + 1, "text": page.get_text("text"), "status": "success"}
        for i, page in enumerate(doc)
    ]
    doc.close()
    jobdir = Path(tempfile.mkdtemp(prefix="noc_test_"))
    (jobdir / "pages.json").write_text(json.dumps({"pages": pages}))
    result = LineItemParser().parse_job_items("t", jobdir, direction="export")
    text = "\n".join(p["text"] for p in pages)
    return result, text


def test_detects_noc_layout():
    for path in (LAURA, ACE):
        if not path.exists():
            continue
        text = "\n".join(p.get_text("text") for p in fitz.open(path))
        assert LineItemParser._is_noc_scientific_packing_list(text)


def test_laura_bassi_extracts_all_hs_rows():
    if not LAURA.exists():
        return
    result, text = _parse_pdf(LAURA)
    items = result.get("items") or []
    assert result.get("format_type") == "noc_scientific"
    assert len(items) == 27  # item 25 has HS "na"
    assert sum(1 for it in items if "Anchor" in it["description"]) == 3
    doc = document_commodity_codes(text)
    parsed = {cn8(it.get("commodity_code")) for it in items}
    assert not (doc - parsed), doc - parsed
    total = sum(float(it["total_value"]) for it in items)
    assert abs(total - 245971.0) < 0.01


def test_ace_scientist_kit_extracts_all_boxes():
    if not ACE.exists():
        return
    result, text = _parse_pdf(ACE)
    items = result.get("items") or []
    assert result.get("format_type") == "noc_scientific"
    assert len(items) >= 100
    boxes = {
        it["description"].split("]")[0] + "]"
        for it in items
        if it["description"].startswith("[")
    }
    assert boxes >= {
        "[ACEJG01]", "[ACEJG02]", "[ACEJG03]",
        "[ACEJG04]", "[ACEJG05]", "[ACEJG06]",
    }
    apollo = [it for it in items if "Apollo" in it["description"]]
    assert len(apollo) == 3
    doc = document_commodity_codes(text)
    parsed = {cn8(it.get("commodity_code")) for it in items}
    assert not (doc - parsed), doc - parsed
    total = sum(float(it["total_value"]) for it in items)
    assert abs(total - 45694.96) < 0.05


def test_truncated_myanmar_is_country():
    assert looks_like_country_token("Myanma")
    assert looks_like_country_token("USA")
    assert looks_like_country_token("Germany")
    assert not looks_like_country_token("Parflux 78H-21 Sediment Trap")
