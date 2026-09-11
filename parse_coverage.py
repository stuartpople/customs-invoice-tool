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


_INV_STOP = frozenset({
    'RECHNUNG', 'FACTURE', 'FACTURA', 'NUMBER', 'DATE', 'REF', 'NO', 'NR', 'NE',
    'N0', 'PAGE', 'ADDRESS', 'TOTAL', 'COMMERCIAL', 'TERMS', 'DUE', 'VAT',
    'FROM', 'THE', 'AND', 'FOR', 'SHIPMENT', 'EXPORT', 'IMPORT', 'VALUE',
    'AMOUNT', 'INVOICE', 'INV', 'SIN', 'GB', 'UK', 'OF', 'TO',
    'CONSIGNEE', 'EXPORTER', 'IMPORTER', 'SHIPPER', 'BUYER', 'SELLER',
    'CUSTOMER', 'SUPPLIER', 'COMPANY', 'ACCOUNT',
})

# Party phrases that contain "invoice" but are not the invoice-number label.
# Mask these; do not skip the whole line — Arrow/two-column PDFs often extract
# "Consignee / Invoice To:" and "Invoice No: INV00017249" on the same row.
_PARTY_PHRASE_RE = re.compile(
    r'consignee\s*/\s*invoice\s*to|'
    r'consignee\s*/\s*invoice|'
    r'\binvoice\s+to\b|'
    r'\binvoice\s+address\b|'
    r'\binvoice\s+for\b|'
    r'\bbill(?:ed)?\s+to\b|'
    r'\bsold\s+to\b|'
    r'\bship(?:ped)?\s+to\b',
    re.IGNORECASE,
)

# Invoice No / Nr / Ne (OCR of No) / Number / Ref / # then the ID.
# Allow a single-letter prefix (S81217) and 4+ digit numbers (88421).
_INV_LABEL_ID_RE = re.compile(
    r'(?:invoice|inv)[\s.]*'
    r'(?:no\.?|nr\.?|n0|ne(?![a-z])|num(?:ber)?|ref(?:erence)?|#)'
    r'(?!\s*(?:to|address|date|due|total|value|from)\b)\s*[:.\-]?\s*'
    r'([A-Za-z]{1,10}[-/]?\d{2,}[A-Za-z0-9\-/]*|\d{4,16})',
    re.IGNORECASE,
)
_INV_LABEL_RE = re.compile(
    r'(?:invoice|inv)[\s.]*'
    r'(?:no\.?|nr\.?|n0|ne(?![a-z])|num(?:ber)?|ref(?:erence)?|#)'
    r'(?!\s*(?:to|address|date|due|total|value|from)\b)',
    re.IGNORECASE,
)
_INV_ID_RE = re.compile(
    r'\b([A-Za-z]{1,10}[-/]?\d{2,}[A-Za-z0-9\-/]*|\d{4,16})\b',
    re.IGNORECASE,
)
_OVERFLOW_PREFIX_RE = re.compile(
    r'^(LENGTH|WIDTH|HEIGHT|DEPTH|BREAKLOAD|COLOUR|COLOR|SIZE|DIMS?|'
    r'WEIGHT|NETT?|GROSS|NOTE[S]?|BATCH|LOT|SERIAL|DESC(?:RIPTION)?)\b',
    re.IGNORECASE,
)


def looks_like_invoice_id(candidate: str) -> bool:
    cand = (candidate or '').strip().strip('.:#')
    if not cand or cand.upper() in _INV_STOP:
        return False
    if not re.search(r'\d', cand):
        return False
    if len(cand) < 4 or len(cand) > 24:
        return False
    if re.search(r'\d{1,2}[./-]\d{1,2}[./-]\d{2,4}', cand):
        return False
    if re.fullmatch(r'[A-Z]{1,10}[-/]?\d{2,}[A-Z0-9\-/]*', cand, re.I):
        return True
    if re.fullmatch(r'\d{4,16}', cand):
        # Phone / VAT / sort-code fragments / a lone year
        if cand.startswith(('1323', '377109', '4470')):
            return False
        if re.fullmatch(r'(?:19|20)\d{2}', cand):
            return False
        return True
    return False


def extract_invoice_number(text: str) -> Optional[str]:
    """Find the commercial invoice number for CDS previous-document (Z/380).

    Reads the standard 'Invoice No:' field even when it shares a PDF row with
    'Consignee / Invoice To:'. OCR 'Invoice Ne' / wrapped 'Invoice\\nNo:' still
    count. Party labels never become the number.
    """
    if not text:
        return None

    def _norm(cand: str) -> str:
        return cand.upper() if re.search(r'[A-Za-z]', cand) else cand

    header = '\n'.join(text.splitlines()[:120])
    masked = _PARTY_PHRASE_RE.sub(' ', header)
    flat = re.sub(r'[\s|]+', ' ', masked).strip()

    for m in _INV_LABEL_ID_RE.finditer(flat):
        cand = m.group(1)
        if looks_like_invoice_id(cand):
            return _norm(cand)

    # Label present but ID on the following tokens (rare leftover spacing)
    label = _INV_LABEL_RE.search(flat)
    if label:
        tail = flat[label.end():label.end() + 80]
        for m in _INV_ID_RE.finditer(tail):
            cand = m.group(1)
            if looks_like_invoice_id(cand):
                return _norm(cand)
    return None


_KNOWN_FOUR_DIGIT_CPC = frozenset({
    '1040', '1000', '2100', '2200', '2300', '3151',
    '4000', '4071', '4200', '4400', '5100', '5171',
    '5300', '6110', '7100',
})


def default_cpc(direction: str) -> str:
    """CDS requested procedure: 1040 permanent export, 4000 free-circulation import."""
    return '1040' if (direction or '').lower() == 'export' else '4000'


def extract_cpc_code(text: str, direction: str = 'export') -> str:
    """CPC from the invoice when stated; otherwise 1040 export / 4000 import.

    Ignores 7-digit strings like Marlow 'CPC: 1000001' (procedure+additional)
    so they are not truncated to 1000.
    """
    blob = text or ''
    labelled = re.search(r'\bCPC\s*:?\s*(\d{4})(?!\d)', blob, re.IGNORECASE)
    if labelled and labelled.group(1) in _KNOWN_FOUR_DIGIT_CPC:
        code = labelled.group(1)
        if (direction or '').lower() == 'export' and code == '4000':
            return '1040'
        if (direction or '').lower() == 'import' and code == '1040':
            return '4000'
        return code
    seven = re.search(r'\bCPC\s*:?\s*(\d{7})\b', blob, re.IGNORECASE)
    if seven:
        raw = seven.group(1)
        if raw.startswith('10'):
            return '1040'
        if raw.startswith('40'):
            return '4000'
    if re.search(r'perm(?:anent)?\s+export|direct\s+export', blob, re.I):
        return '1040'
    return default_cpc(direction)


def line_has_commodity_hs(line: str) -> bool:
    for m in _HS_RE.finditer(line or ''):
        if is_commodity_hs(m.group(1)):
            return True
    return False


def is_table_overflow_line(line: str, previous_has_hs: bool = True) -> bool:
    """True when a table row is wrapped description, not a new item.

    A new item has its own HS code (or qty+UoM+money). Overflow is LENGTH:/
    BREAKLOAD, a continuation sentence, or any SKU-like start with no HS
    once the previous item already captured its code.
    """
    s = (line or '').strip().lstrip('~-_|• ')
    if not s:
        return True
    if line_has_commodity_hs(s):
        return False
    if _QTY_UOM_RE.search(s) and _MONEY_RE.search(s):
        return False
    if _OVERFLOW_PREFIX_RE.match(s) or re.match(r'^[a-z(]', s):
        return True
    # No HS on this row and the line above already had one → wrapped cell
    return bool(previous_has_hs)


def merge_overflow_items(items: List[Dict]) -> List[Dict]:
    """Fold HS-less wrap rows into the previous goods line."""
    if not items:
        return items
    out: List[Dict] = []
    for it in items:
        code = cn8(it.get('commodity_code') or it.get('hs_code') or '')
        desc = (it.get('description') or '').strip()
        tot = parse_money(str(it.get('total_value') or ''))
        if len(code) == 8 and is_commodity_hs(code):
            out.append(it)
            continue
        if out and (not tot or is_table_overflow_line(desc, previous_has_hs=True)):
            prev = out[-1]
            extra = desc or (it.get('stock_number') or '')
            if extra and extra.lower() not in (prev.get('description') or '').lower():
                prev['description'] = ((prev.get('description') or '') + ' ' + extra).strip()
            continue
        out.append(it)
    return out
