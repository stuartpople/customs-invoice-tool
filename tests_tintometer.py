"""Tintometer / Lovibond commercial invoice parser."""
from parse_coverage import document_commodity_codes, looks_like_bank_account, items_total
from line_item_parser import LineItemParser


SAMPLE = """
The Tintometer Ltd
Lovibond House Commercial Invoice 15052
Doc Ref: INV2EX
Our Product | Customer Commodity Total
132750 1 3926.90.9700 Plastic Cell 10mm 4 Clear Sides PK 1.00 50.49 0.00 50.49 ROW
Net Weight 0.25000 Total Net Weight 0.25000 Country of Origin: CN UN Code
134000 2 3822.90.0000 Lq'd Ref Std ASTM Value 1(500 EA 1.00 154,06 0.00 154.06 ROW
Net Weight 0.76000 Total Net Weight 0.76000 Country of Origin: GB UN Code
134010 3 3822.90.0000 Lq'd Ref Std ASTM Value 3(500 EA 1.00 154.06 0.00 154.06 ROW
Net Weight 0.78000 Total Net Weight 0.78000 Country of Origin: GB UN Code
656030 14 7017.20.0000 W600/8/100 mm Cell for EA 5.00 146.32 0.00 731,62 ROW
Tintometer
Net Weight 0.08000 Total Net Weight 0.40000 Country of Origin: GB UN Code
Dil Chrg, Actual 838.500
GBP: 40-42-18 32871957, USD: 40-12-76 74882967, EUR; 40-12-76 74883279 All Prices are in: Pound Sterling
Invoice Total £ 3241.63
HSBC, 55 Above Bar Street, Southampton Swift Code: HBUKGB4110G
"""


def test_bank_accounts_not_treated_as_hs():
    assert looks_like_bank_account(
        "74882967",
        "GBP: 40-42-18 32871957, USD: 40-12-76 74882967, EUR; 40-12-76 74883279",
    )
    codes = document_commodity_codes(SAMPLE)
    assert "74882967" not in codes
    assert "74883279" not in codes
    assert "32871957" not in codes
    assert "38229000" in codes
    assert "39269097" in codes
    assert "70172000" in codes


def test_tintometer_parser_reads_european_commas_and_coo():
    parser = LineItemParser()
    assert parser._is_tintometer_invoice(SAMPLE)
    items = parser._parse_tintometer_format(SAMPLE.splitlines(), "export", {})
    assert len(items) == 4
    by_stock = {it["stock_number"]: it for it in items}
    assert by_stock["134000"]["total_value"] == "154.06"
    assert by_stock["134000"]["quantity"] == "1"
    assert by_stock["134000"]["commodity_code"] == "38229000"
    assert by_stock["134000"]["country_of_origin"] == "GB"
    assert by_stock["656030"]["total_value"] == "731.62"
    assert by_stock["656030"]["quantity"] == "5"
    assert by_stock["132750"]["country_of_origin"] == "CN"
    assert abs(items_total(items) - (50.49 + 154.06 + 154.06 + 731.62)) < 0.05
    # Remittance must not become a line
    assert not any(str(it.get("commodity_code") or "").startswith("7488") for it in items)


def test_end_to_end_pack_result_skips_bank_harvest():
    import json
    import tempfile
    from pathlib import Path

    pages = [{"page_number": 1, "text": SAMPLE, "status": "success"}]
    jobdir = Path(tempfile.mkdtemp(prefix="tinto_test_"))
    (jobdir / "pages.json").write_text(json.dumps({"pages": pages}))
    result = LineItemParser().parse_job_items("t", jobdir, direction="export")
    assert result.get("format_type") == "tintometer"
    items = result.get("items") or []
    assert len(items) == 4
    codes = {str(it.get("commodity_code") or "") for it in items}
    assert "74882967" not in codes
    assert "74883279" not in codes
    warn = result.get("parse_warning") or ""
    assert "74882967" not in warn
    assert "74883279" not in warn
