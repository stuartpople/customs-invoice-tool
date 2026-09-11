"""Layout-agnostic parse coverage.

Dedicated vendor parsers must not 'succeed' with a subset of the HS codes
that are actually printed on the invoice. These helpers scan the OCR/text
for commodity-looking codes and invoice totals so a missed layout still
gets a second pass (generic row harvest) and a visible warning.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple


_JUNK_LINE_RE = re.compile(
    r'account\s*no|sort\s*code|iban|swift|vat\s*no|eori|'
    r'phone\s*:|fax\s*:|invoice\s*no|invoice\s*date|invoice\s+due|'
    r'consignee|exporter:|page\s+\d+\s+of|tax\s+exclusive|'
    r'total\s+(gbp|usd|eur)\s+incl|grand\s+total|payment\s+terms|'
    r'barclays|approved\s+exporter',
    re.IGNORECASE,
)

_HS_RE = re.compile(r'(?<!\d)(\d{8,10})(?!\d)')
_UOM_RE = re.compile(r'\b(EA|PCS|PC|SET|KG|BAG|BOX|PK|PACK|M|REEL|ROLL)\b', re.IGNORECASE)
_MONEY_RE = re.compile(r'[\d]+[.,]\d{2}')
_SKU_RE = re.compile(r'^[\|~\-\s_•]*([A-Z]{2,4}[A-Z0-9]{3,6})\b', re.IGNORECASE)
_QTY_UOM_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*\|?\s*(EA|PCS|PC|SET|KG|BAG|BOX|PK|PACK|M|REEL|ROLL)\b',
    re.IGNORECASE,
)

# VAT / phone / sort-code fragments that OCR presents as 8–10 digits
_JUNK_HS_PREFIXES = ('377109', '13234444', '209887', '33404927')


def is_commodity_hs(code: str) -> bool:
    if not code or not str(code).isdigit():
        return False
    digits = str(code)
    if len(digits) not in (8, 10):
        return False
    if any(digits.startswith(p) for p in _JUNK_HS_PREFIXES):
        return False
    chapter = int(digits[:2])
    return 1 <= chapter <= 97 or chapter == 99


def cn8(code: str) -> str:
    digits = re.sub(r'\D', '', str(code or ''))
    return digits[:8] if len(digits) >= 8 else digits


def document_commodity_codes(text: str) -> Set[str]:
    """CN8 codes that look like real tariff lines, not bank/VAT/phone numbers."""
    counts: Dict[str, int] = {}
    signalled: Set[str] = set()
    for line in (text or '').splitlines():
        if _JUNK_LINE_RE.search(line):
            continue
        has_signal = bool(_UOM_RE.search(line) or _MONEY_RE.search(line))
        for m in _HS_RE.finditer(line):
            raw = m.group(1)
            if not is_commodity_hs(raw):
                continue
            code = raw[:8]
            counts[code] = counts.get(code, 0) + 1
            if has_signal:
                signalled.add(code)
    kept = set()
    for code, n in counts.items():
        if code in signalled or n >= 2:
            kept.add(code)
    return kept


def parse_money(token: str) -> Optional[float]:
    if not token:
        return None
    t = token.strip()
    if t.count(',') == 1 and '.' not in t:
        t = t.replace(',', '.')
    else:
        t = t.replace(',', '')
    try:
        return float(t)
    except ValueError:
        return None


def invoice_total_hint(text: str) -> Optional[float]:
    """Best-effort invoice grand total from footer text."""
    patterns = (
        r'Total\s+(?:GBP|USD|EUR)\s+Incl\.?\s*VAT\s*([\d,]+(?:\.\d{2})?)',
        r'Tax\s+Exclusive(?:\s+Value)?[^\d]{0,40}([\d,]+(?:\.\d{2})?)',
        r'(?:Grand\s+)?Invoice\s+Total[^\d]{0,20}([\d,]+(?:\.\d{2})?)',
        r'Total\s+(?:GBP|USD|EUR)\s*:?\s*([\d,]+\.\d{2})',
        r'Tax Exclusive\s+([\d,]+(?:\.\d{2})?)',
    )
    found: List[float] = []
    blob = text or ''
    for pat in patterns:
        for m in re.finditer(pat, blob, re.IGNORECASE):
            val = parse_money(m.group(1))
            if val and val > 0:
                found.append(val)
    if not found:
        return None
    # Prefer the largest plausible total (VAT 0.00 and line amounts also match)
    return max(found)


def items_total(items: Iterable[Dict]) -> float:
    total = 0.0
    for it in items or []:
        val = parse_money(str(it.get('total_value') or it.get('value') or ''))
        if val:
            total += val
    return round(total, 2)


def item_cn8s(items: Iterable[Dict]) -> Set[str]:
    out: Set[str] = set()
    for it in items or []:
        code = cn8(it.get('commodity_code') or it.get('hs_code') or '')
        if len(code) == 8 and is_commodity_hs(code):
            out.add(code)
    return out


def item_key(it: Dict) -> Tuple:
    tot = parse_money(str(it.get('total_value') or '')) or 0.0
    sku = (it.get('stock_number') or '').upper().replace('O', '0')
    return (sku, cn8(it.get('commodity_code') or ''), str(it.get('quantity') or ''), f'{tot:.2f}')


def harvest_hs_rows(
    lines: List[str],
    *,
    pad_hs,
    page_at,
    existing_keys: Optional[Set[Tuple]] = None,
    currency: str = 'GBP',
) -> List[Dict]:
    """Pull a line item from any OCR row that contains a commodity HS code.

    Not vendor-specific: SKU … qty UoM HS … money. Used when a dedicated
    parser recognised the layout but dropped rows.
    """
    existing_keys = existing_keys or set()
    items: List[Dict] = []
    for i, raw in enumerate(lines):
        line = (raw or '').strip()
        if not line or _JUNK_LINE_RE.search(line):
            continue
        hs_m = None
        for m in _HS_RE.finditer(line):
            if is_commodity_hs(m.group(1)):
                hs_m = m
                break
        if not hs_m:
            continue
        before = line[:hs_m.start()].strip(' -|~_')
        after = line[hs_m.end():]
        qty, uom = '1', 'EA'
        um = list(_QTY_UOM_RE.finditer(before))
        if um:
            qty, uom = um[-1].group(1), um[-1].group(2).upper()
            before = before[:um[-1].start()].strip(' -|~_')
        sku = ''
        sku_m = _SKU_RE.match(line)
        if sku_m:
            sku = sku_m.group(1).upper()
            if before.upper().startswith(sku):
                before = before[len(sku):].strip(' -|~_')
        money_vals = []
        for tok in _MONEY_RE.findall(after):
            norm = f'{parse_money(tok):.2f}' if parse_money(tok) is not None else ''
            if norm:
                money_vals.append(norm)
        total_value = money_vals[-1] if money_vals else ''
        unit_value = money_vals[-2] if len(money_vals) >= 2 else total_value
        coo = ''
        coo_m = re.match(r'\s*\|?\s*([A-Z]{2})\b', after)
        if coo_m:
            coo = coo_m.group(1)
        hs_code = pad_hs(hs_m.group(1))
        cand = {
            'line_number': str(len(items) + 1),
            'stock_number': sku,
            'description': before or sku or f'HS {hs_m.group(1)[:8]}',
            'quantity': qty,
            'uom': uom,
            'unit_value': unit_value,
            'total_value': total_value,
            'currency': currency,
            'commodity_code': hs_code,
            'country_of_origin': coo or 'GB',
            'net_weight': '',
            'unit_weight': '',
            'hs_code': hs_code,
            'pages': [page_at(i)],
            'confidence': 0.72,
            'needs_review': True,
            'review_notes': 'Recovered by HS-coverage harvest (layout parser missed this row)',
            'raw_text': line[:200],
        }
        key = item_key(cand)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        items.append(cand)
    return items


def coverage_warnings(
    items: List[Dict],
    text: str,
    *,
    total_tolerance: float = 0.08,
) -> Tuple[Set[str], Set[str], List[str]]:
    doc_hs = document_commodity_codes(text)
    parsed_hs = item_cn8s(items)
    missing = doc_hs - parsed_hs
    warnings: List[str] = []
    if missing:
        warnings.append(
            'Invoice text contains HS codes that were not parsed: '
            + ', '.join(sorted(missing))
            + f' (found on document: {", ".join(sorted(doc_hs)) or "none"}).'
        )
    inv_total = invoice_total_hint(text)
    line_sum = items_total(items)
    if inv_total and inv_total > 1:
        delta = abs(inv_total - line_sum)
        gap = delta / inv_total
        if delta > 5 and gap > min(total_tolerance, 0.02):
            warnings.append(
                f'Line totals {line_sum:.2f} do not match invoice total {inv_total:.2f} '
                f'({gap:.0%} off) — rows are probably missing.'
            )
    return doc_hs, parsed_hs, warnings
