"""HMRC 429/5xx must not look like obsolete CN8 remaps."""
from hmrc_api import HMRCTariffAPI


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_try_commodity_distinguishes_404_from_429():
    api = HMRCTariffAPI()
    api._http_get = lambda url, timeout=15, retries=3: _Resp(404)  # type: ignore
    assert api._try_commodity('8504405500') is None

    api._http_get = lambda url, timeout=15, retries=3: _Resp(429)  # type: ignore
    res = api._try_commodity('8504405500')
    assert res and res.get('_unavailable')
    assert not HMRCTariffAPI._commodity_usable(res)
    assert HMRCTariffAPI._commodity_unavailable(res)


def test_validate_keeps_code_when_hmrc_unavailable():
    HMRCTariffAPI.clear_caches()
    api = HMRCTariffAPI()
    api._http_get = lambda url, timeout=15, retries=3: _Resp(503)  # type: ignore
    v = api.validate_commodity_code('85364900', direction='export')
    assert v.get('valid') is True
    assert v.get('unverified') is True
    assert not v.get('suggested_code')


def _safe_hs_autocorrections(items, hs_validation, direction):
    """Mirror of app._safe_hs_autocorrections for unit testing without Streamlit."""
    is_export = direction.lower() == 'export'
    invalid_codes = {c: v for c, v in (hs_validation or {}).items()
                     if not v.get('valid', True)}
    same_cn8, obsolete, descs, still_invalid = {}, {}, {}, {}
    for code, result in invalid_codes.items():
        if result.get('unverified'):
            continue
        resolved = (result.get('resolved_code') or '').replace(' ', '')
        suggested = (result.get('suggested_code') or '').replace(' ', '')
        clean = (code or '').replace(' ', '').replace('-', '').replace('.', '')
        if resolved and clean and len(clean) >= 8 and resolved[:8] == clean[:8]:
            new_code = resolved[:8] if is_export else resolved
            if new_code != code:
                same_cn8[code] = new_code
                descs[code] = result.get('description', '')
            continue
        if suggested and clean and suggested != clean[:len(suggested)]:
            new_code = suggested[:8] if is_export else suggested[:10].ljust(10, '0')
            obsolete[code] = new_code
            descs[code] = (
                result.get('suggested_description') or result.get('description', '')
            )
            continue
        still_invalid[code] = result

    if same_cn8:
        for item in items:
            cc = item.get('commodity_code', '')
            if cc in same_cn8:
                item['commodity_code'] = same_cn8[cc]

    if obsolete:
        for item in items:
            cc = item.get('commodity_code', '')
            if cc not in obsolete:
                continue
            old = cc
            item['commodity_code'] = obsolete[cc]
            item['needs_review'] = True
            note = (
                f"Obsolete HS {old} reclassified to {obsolete[cc]} "
                f"({descs.get(old, 'tariff replacement')}) — confirm"
            )
            existing = item.get('review_notes', '')
            if note not in (existing or ''):
                item['review_notes'] = f"{existing}; {note}" if existing else note

    for code, result in still_invalid.items():
        for item in items:
            if item.get('commodity_code') != code:
                continue
            item['needs_review'] = True
            existing = item.get('review_notes', '')
            msg = result.get('error', 'HS code needs manual review')
            if msg not in (existing or ''):
                item['review_notes'] = f"{existing}; {msg}" if existing else msg

    return same_cn8, obsolete, still_invalid, descs


def test_safe_autocorrect_import_same_cn8_quiet_and_obsolete_flagged():
    items = [
        {'commodity_code': '38101000', 'needs_review': False},
        {'commodity_code': '85044055', 'needs_review': False},
        {'commodity_code': '85364900', 'needs_review': False},
    ]
    hs_validation = {
        '38101000': {
            'valid': False,
            'resolved_code': '3810100010',
            'description': 'Other',
        },
        '85044055': {
            'valid': False,
            'suggested_code': '85044060',
            'suggested_description': 'Other',
        },
        '85364900': {
            'valid': True,
            'unverified': True,
            'resolved_code': '8536490000',
        },
    }
    same_cn8, obsolete, still_invalid, _descs = _safe_hs_autocorrections(
        items, hs_validation, 'import'
    )
    assert same_cn8 == {'38101000': '3810100010'}
    assert items[0]['commodity_code'] == '3810100010'
    assert items[0].get('needs_review') is False
    assert not items[0].get('review_notes')
    assert obsolete == {'85044055': '8504406000'}
    assert items[1]['needs_review'] is True
    assert 'Obsolete HS' in (items[1].get('review_notes') or '')
    assert still_invalid == {}
    assert items[2]['commodity_code'] == '85364900'


if __name__ == '__main__':
    test_try_commodity_distinguishes_404_from_429()
    test_validate_keeps_code_when_hmrc_unavailable()
    test_safe_autocorrect_import_same_cn8_quiet_and_obsolete_flagged()
    print('ok')
