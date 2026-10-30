"""Arrow Export: group (cc:) header applies to child lines without their own code."""
from line_item_parser import LineItemParser

# PyMuPDF-style column extract for INV00017388 (Parts header shared by items 1–3)
SAMPLE = """
Arrow Export
Invoice No: INV00017388
Currency: British Pound
MNT26-09-101442 ~ SORD27002
Parts (cc:8418999099)
910.50
1.
91.05
SE impulse relay iTLs - 1P - 1NO - 16A - coil 110 VDC - 230...240
VAC 50/60Hz
Local distributor code: 396735911 A9C32811
Nos
10.00
151.70
2.
15.17
Finder Power Relay, 230V ac Coil, 7A Switching Current, 4PDT
Mfr Part No.55.34.8.230.0040, RS Stock No.385-957
Nos
10.00
86.50
3.
8.65
Finder 94 14 Way 250V ac DIN Rail Relay Socket, for use with
55.34 Series Relay
Mfr Part No.94.74SMA, RS Stock No.189-3263
Nos
10.00
MGT26-10-1041842 ~ SORD26948
4588.50
4.
917.70
Western Digital Ultrastar WUH722018CLE6L4 18 TB Hard
Drive - 3.5" Internal - SATA
(cc:8523291500)
Nos
5.00
MGT26-10-1041849 ~ SORD27109 (part order)
2014.80
5.
335.80
Crucial CT32G4DFD832A 32 GB 1 x 32 GB, DDR4 3200 MHz
(cc:8548009099)
Nos
6.00
611.80
6.
305.90
Crucial CT16G56C46U5 16 GB 1 x 16 GB, DDR5 5600 Mhz
(cc:8548009099)
Nos
2.00
Total CPT Airfreight Mahe
8598.80
"""


def test_arrow_group_cc_inherited_by_child_lines():
    parser = LineItemParser()
    items = parser._parse_arrow_export_format(SAMPLE.splitlines(), "export", {})
    assert len(items) == 6
    by_line = {str(it["line_number"]).rstrip("."): it for it in items}
    assert by_line["1"]["commodity_code"] == "84189990"
    assert by_line["2"]["commodity_code"] == "84189990"
    assert by_line["3"]["commodity_code"] == "84189990"
    assert by_line["4"]["commodity_code"] == "85232915"
    assert by_line["5"]["commodity_code"] == "85480090"
    assert by_line["6"]["commodity_code"] == "85480090"
    assert abs(float(by_line["1"]["total_value"]) - 910.50) < 0.01
    assert abs(float(by_line["4"]["total_value"]) - 4588.50) < 0.01
    # Order-ref lines must not pollute descriptions
    assert "MGT26" not in by_line["3"]["description"]
    assert "SORD" not in by_line["3"]["description"]
