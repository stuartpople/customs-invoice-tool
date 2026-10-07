"""Country dropdown: dedupe + ISO labels for search by name or code."""
from countries import (
    COMMON_COUNTRIES,
    country_select_options,
    format_country_option,
    parse_country_selection,
    selection_to_iso,
)


def test_format_includes_iso():
    assert format_country_option("United Kingdom") == "United Kingdom (GB)"
    assert format_country_option("China") == "China (CN)"
    assert format_country_option("---") == "---"
    assert format_country_option("") == ""


def test_options_dedupe_common_from_full_list():
    opts = country_select_options()
    assert opts[0] == ""
    assert "---" in opts
    labels = [o for o in opts if o and o != "---"]
    # Each ISO suffix appears at most once for common partners
    for name in COMMON_COUNTRIES:
        label = format_country_option(name)
        assert labels.count(label) == 1, f"duplicate option for {name!r}"


def test_search_by_iso_substring_present():
    opts = country_select_options()
    joined = "\n".join(opts)
    assert "(GB)" in joined
    assert "(CN)" in joined
    assert "(US)" in joined


def test_parse_labeled_and_plain():
    assert parse_country_selection("United Kingdom (GB)") == "United Kingdom"
    assert parse_country_selection("United Kingdom") == "United Kingdom"
    assert parse_country_selection("GB") == "United Kingdom"
    assert parse_country_selection("UK") == "United Kingdom"
    assert parse_country_selection("---") == ""
    assert parse_country_selection("") == ""
    assert parse_country_selection("Sint Maarten (Dutch part)") == "Sint Maarten (Dutch part)"


def test_selection_to_iso():
    assert selection_to_iso("United Kingdom (GB)") == "GB"
    assert selection_to_iso("China") == "CN"
    assert selection_to_iso("") is None
    assert selection_to_iso("---") is None
