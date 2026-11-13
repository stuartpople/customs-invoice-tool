"""EU / region origin codes must be flagged for the user to pick a real country."""
from countries import (
    flag_invalid_origin_countries,
    is_valid_country_iso,
    non_country_origin_reason,
    normalize_country_iso,
    normalize_item_country_fields,
)


def test_eu_is_not_a_valid_iso_country():
    assert not is_valid_country_iso('EU')
    assert is_valid_country_iso('DE')
    assert is_valid_country_iso('GB')
    assert non_country_origin_reason('EU')
    assert 'Member State' in non_country_origin_reason('EU')


def test_normalize_keeps_eu_visible_not_silently_blank():
    assert normalize_country_iso('EU') == 'EU'
    assert normalize_country_iso('eu') == 'EU'
    assert normalize_country_iso('European Union') == 'EU'
    # Must not invent a fake ISO by truncating names
    assert normalize_country_iso('European Union') != 'DE'
    assert normalize_country_iso('Germany') == 'DE'


def test_two_letter_must_be_in_hmrc_list():
    # Bug fix: previously any 2 alpha letters (including EU) were accepted
    assert normalize_country_iso('CN') == 'CN'
    assert not is_valid_country_iso(normalize_country_iso('EU'))


def test_line_item_with_eu_origin_needs_review():
    item = {
        'description': 'Widget',
        'commodity_code': '84713000',
        'country_of_origin': 'EU',
        'needs_review': False,
    }
    normalize_item_country_fields(item)
    assert item['country_of_origin'] == 'EU'
    assert item['needs_review'] is True
    assert 'European Union' in (item.get('review_notes') or '') or 'Member State' in (
        item.get('review_notes') or ''
    )


def test_flag_european_union_name():
    item = {'country_of_origin': 'European Union'}
    normalize_item_country_fields(item)
    assert item['country_of_origin'] == 'EU'
    assert item['needs_review'] is True


def test_valid_origin_not_flagged():
    item = {'country_of_origin': 'DE', 'needs_review': False}
    flag_invalid_origin_countries(normalize_item_country_fields(item))
    assert item['needs_review'] is False
    assert not item.get('review_notes')


if __name__ == '__main__':
    test_eu_is_not_a_valid_iso_country()
    test_normalize_keeps_eu_visible_not_silently_blank()
    test_two_letter_must_be_in_hmrc_list()
    test_line_item_with_eu_origin_needs_review()
    test_flag_european_union_name()
    test_valid_origin_not_flagged()
    print('ok')
