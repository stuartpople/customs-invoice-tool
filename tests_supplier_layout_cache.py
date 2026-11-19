"""Supplier fingerprint → dedicated parser cache."""
import json
from pathlib import Path

import supplier_layout_cache as cache


ARROW_SNIPPET = """
Arrow Export
Unit 3
www.arrowexport.co.uk
e: keith@arrowexport.co.uk
EORI No: GB323559458000
VAT Registration No. 323 5594 58
70.40
1.
70.40
Next B by Ted Baker Plunge Swimsuit
G16-669 (cc:6112419000)
Nos
1.00
"""


def setup_function(_fn=None):
    if cache.DB_PATH.exists():
        cache.DB_PATH.unlink()


def test_extract_fingerprint_prefers_eori():
    fp = cache.extract_supplier_fingerprint(ARROW_SNIPPET)
    assert fp["fingerprint"] == "eori:GB323559458000"
    assert fp["fingerprint_kind"] == "eori"
    assert fp["domain"] == "arrowexport.co.uk"
    assert fp["label"] == "Arrow Export"


def test_record_and_cache_hit_then_regen_flag():
    setup_function()
    fp = cache.extract_supplier_fingerprint(ARROW_SNIPPET)
    cache.record_parse_success(fp, "arrow_export", source="detect")
    hit = cache.get_cached_parser(fp["fingerprint"])
    assert hit is not None
    assert hit["parser_id"] == "arrow_export"
    assert int(hit["success_count"]) == 1
    assert int(hit["needs_regen"]) == 0

    cache.record_export_corrections(
        fp["fingerprint"], parser_id="arrow_export", fields_changed=1
    )
    cache.record_export_corrections(
        fp["fingerprint"], parser_id="arrow_export", fields_changed=2
    )
    hit2 = cache.get_cached_parser(fp["fingerprint"])
    assert int(hit2["correction_count"]) >= 2
    assert int(hit2["needs_regen"]) == 1

    # Successful dedicated parse clears the flag
    cache.record_parse_success(fp, "arrow_export", source="detect")
    hit3 = cache.get_cached_parser(fp["fingerprint"])
    assert int(hit3["needs_regen"]) == 0


def test_count_item_field_edits():
    original = [
        {"line_number": "1", "commodity_code": "61124190", "total_value": "70.40"},
        {"line_number": "2", "commodity_code": "61045200", "total_value": "39.60"},
    ]
    edited = [
        {"line_number": "1", "commodity_code": "61124190", "total_value": "70.40"},
        {"line_number": "2", "commodity_code": "61045200", "total_value": "40.00"},
    ]
    stats = cache.count_item_field_edits(original, edited)
    assert stats["fields_changed"] == 1


def test_cache_dispatch_arrow_pdf_if_present():
    setup_function()
    try:
        import fitz
    except ImportError:
        return
    pdf = Path(
        "/Users/stuartpople/.cursor/projects/Users-stuartpople-logisticore/"
        "attachments/9859445d-1ddd-42b8-9a2f-4f95132db15b/Invoice_INV00017403.pdf"
    )
    if not pdf.exists():
        return
    from line_item_parser import LineItemParser

    text = "\n".join(p.get_text() for p in fitz.open(str(pdf)))
    fp = cache.extract_supplier_fingerprint(text)
    assert fp["fingerprint"]
    cache.record_parse_success(fp, "arrow_export")

    parser = LineItemParser()
    pages = {
        "pages": [{"page_number": 1, "status": "success", "text": text}],
    }
    # Simulate pages.json job
    import tempfile, shutil
    job = Path(tempfile.mkdtemp()) / "job"
    job.mkdir()
    (job / "pages.json").write_text(json.dumps(pages))
    (job / "metadata.json").write_text(json.dumps({"pdf_path": str(pdf)}))
    try:
        result = parser.parse_job_items("t", job, "export")
        assert result.get("format_type") == "arrow_export"
        assert result.get("layout_cache_hit") is True
        assert len(result.get("items") or []) == 41
    finally:
        shutil.rmtree(job.parent)


if __name__ == "__main__":
    setup_function()
    test_extract_fingerprint_prefers_eori()
    test_record_and_cache_hit_then_regen_flag()
    test_count_item_field_edits()
    test_cache_dispatch_arrow_pdf_if_present()
    print("ok")
