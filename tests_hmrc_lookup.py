"""Export CN8 lookup: residual 89/98 pads, not NAR from specialised …10."""
from hmrc_api import EXPORT_RESIDUAL_SUFFIXES, HMRCTariffAPI


def test_residual_suffixes_include_uk_other_pads():
    assert EXPORT_RESIDUAL_SUFFIXES[:3] == ('00', '90', '99')
    assert '89' in EXPORT_RESIDUAL_SUFFIXES
    assert '98' in EXPORT_RESIDUAL_SUFFIXES
    assert '10' not in EXPORT_RESIDUAL_SUFFIXES


def test_relay_and_wiring_cn8s_resolve_doc_codes():
    """These used to 404 on 00/90/99 and export as N/A / LOOKUP FAILED."""
    HMRCTariffAPI.clear_caches()
    api = HMRCTariffAPI()
    for code, expect_taric in [
        ('85364190', '8536419089'),
        ('85443000', '8544300089'),
        ('90258040', '9025804089'),
    ]:
        d = api.get_commodity_details(
            code, direction='export', destination_country='AZ', export_only=True)
        assert d, code
        assert not d.get('error'), (code, d.get('error'))
        docs = d.get('selected_document_codes') or d.get('document_codes') or {}
        assert docs, (code, 'no document codes', d.get('resolved_taric_code'))
        taric = d.get('resolved_taric_code') or ''
        assert taric == expect_taric, (code, taric)
        assert not d.get('specialised_taric_fallback'), (code, d.get('specialised_taric_fallback'))


def test_parts_cn8_no_longer_404s_on_residual_89():
    """Lookup used to fail entirely; empty AZ export-only docs is a filter, not a 404."""
    HMRCTariffAPI.clear_caches()
    api = HMRCTariffAPI()
    d = api.get_commodity_details(
        '85389091', direction='export', destination_country=None, export_only=True)
    assert not d.get('error'), d.get('error')
    assert (d.get('resolved_taric_code') or '').endswith('89')
    docs = d.get('selected_document_codes') or d.get('document_codes') or {}
    assert docs


def test_power_cord_still_uses_residual_other_not_nar_leaf():
    HMRCTariffAPI.clear_caches()
    api = HMRCTariffAPI()
    d = api.get_commodity_details(
        '85444290', direction='export', destination_country=None, export_only=True)
    assert not d.get('error'), d.get('error')
    assert (d.get('resolved_taric_code') or '').endswith('90')
    assert not d.get('specialised_taric_fallback')
    docs = d.get('selected_document_codes') or d.get('document_codes') or {}
    assert docs


def test_signalling_glass_still_gets_9y10():
    HMRCTariffAPI.clear_caches()
    api = HMRCTariffAPI()
    d = api.get_commodity_details(
        '70140000', direction='export', destination_country=None, export_only=True)
    docs = d.get('selected_document_codes') or d.get('document_codes') or {}
    assert '9Y10' in docs
