"""HS code formatting helpers — no local imports (safe for Streamlit redeploys)."""
import re
from typing import Dict, Optional


def hs_digits(code) -> str:
    """Strip a commodity code down to digits only."""
    return re.sub(r'\D', '', str(code or ''))


def format_hs_for_sheet(code, direction: str = 'export') -> str:
    """HS code as written on the CDS worksheet.

    Export = CN8 only. Import = TARIC 10 (pad with zeros if shorter).
    """
    digits = hs_digits(code)
    if not digits:
        return ''
    if (direction or 'export').lower() == 'export':
        return digits[:8]
    if len(digits) >= 10:
        return digits[:10]
    return digits.ljust(10, '0')


def resolve_hmrc_data(hmrc_data: Optional[Dict], code) -> Dict:
    """Find an HMRC cache entry for a code, ignoring 8-vs-10 key mismatches."""
    if not hmrc_data:
        return {}
    digits = hs_digits(code)
    if not digits:
        return {}
    cn8 = digits[:8]
    candidates = [
        digits,
        cn8,
        cn8 + '00',
        cn8 + '90',
        cn8 + '99',
        digits.ljust(10, '0')[:10],
    ]
    for key in candidates:
        hit = hmrc_data.get(key)
        if hit and not hit.get('error'):
            return hit
    for key in candidates:
        if key in hmrc_data:
            return hmrc_data[key] or {}
    for key, hit in hmrc_data.items():
        kd = hs_digits(key)
        if kd.startswith(cn8) and hit and not hit.get('error'):
            return hit
    return {}
