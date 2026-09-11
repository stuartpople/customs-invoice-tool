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
    r'account\s*(?:number|no\.?)(?:\b|:)|sort\s*code|iban|swift|vat\s*no|eori|'
    r'bank\s+information|bank\s+details|please\s+remit|'
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
    if not (1 <= chapter <= 97 or chapter == 99):
        return False
    # Chapter 70 (glass) ends at heading 7020 — 70216529 is a bank account, not HS
    heading = int(digits[:4])
    if chapter == 70 and heading > 7020:
        return False
    return True


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
        # Remittance footers: "Account Number: 70216529" / IBAN tails
        if looks_like_bank_account(
            next(
                (m.group(1) for m in _HS_RE.finditer(line) if len(m.group(1)) in (8, 10)),
                '',
            ),
            line,
        ):
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
    'CUSTOMER', 'SUPPLIER', 'COMPANY', 'ACCOUNT', 'ORDER', 'PO',
})

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

# Invoice No / Nr / Ne (OCR of No) / Number: / # — not Invoice To / Date,
# and not "quote invoice number on payments".
_INV_LABEL_RE = re.compile(
    r'(?:invoice|inv)[\s.]*'
    r'(?:no\.?|nr\.?|n0|ne(?![a-z])|#|num(?:ber)?\s*:|num(?:ber)?(?!\s+(?:on|as|for|must|should|when|with|to|in|at|and|or|if|please|of)\b))'
    r'(?!\s*(?:to|address|date|due|total|value|from)\b)',
    re.IGNORECASE,
)
_INV_ID_RE = re.compile(
    r'\b([A-Za-z]{1,10}[-/]?\d{2,}[A-Za-z0-9\-/]*|\d{4,16})\b',
    re.IGNORECASE,
)
_INV_SPACED_RE = re.compile(r'\b([A-Za-z]{1,8})\s+(\d{4,16})\b')
_POSTCODE_OUTWARD_RE = re.compile(r'^[A-Z]{1,2}\d{1,2}[A-Z]?$', re.IGNORECASE)
_PO_ID_RE = re.compile(r'^(?:P\.?O\.?|PO)[-/]?\d', re.IGNORECASE)
_FALSE_LABEL_BEFORE_RE = re.compile(
    r'(?:please\s+)?(?:quote|use|using|cite|mention|enter|put)\b|'
    r'\b(?:bank|sort\s*code|iban|swift|bic)\b',
    re.IGNORECASE,
)
_BANK_CONTEXT_RE = re.compile(
    r'(?:bank\s+details|bank\s+information|sort\s*code|\biban\b|\bswift\b|\bbic\b|'
    r'a/?c\s*no|account\s*(?:number|no\.?)|barclays|hsbc|natwest|lloyds|clyde|'
    r'bank\s*:)',
    re.IGNORECASE,
)
_COL_HEADERS = frozenset({
    'tax', 'point', 'page', 'of', '1', 'number', 'no', 'nr', 'ne', 'n0',
    'account', 'acc', 'a/c', 'a/c.', 'date', 'your', 'our', 'ref', 'reference',
    'order', 'vat', 'code', 'customer', 'delivery', 'terms', 'currency',
})
_OVERFLOW_PREFIX_RE = re.compile(
    r'^(LENGTH|WIDTH|HEIGHT|DEPTH|BREAKLOAD|COLOUR|COLOR|SIZE|DIMS?|'
    r'WEIGHT|NETT?|GROSS|NOTE[S]?|BATCH|LOT|SERIAL|DESC(?:RIPTION)?)\b',
    re.IGNORECASE,
)


def looks_like_po(candidate: str) -> bool:
    cand = (candidate or '').strip().strip('.:#')
    return bool(_PO_ID_RE.match(cand))


def looks_like_bank_account(candidate: str, window: str = '') -> bool:
    """UK bank account (8 digits) next to sort code / IBAN / Bank details."""
    cand = (candidate or '').strip()
    if not re.fullmatch(r'\d{8}', cand):
        return False
    return bool(_BANK_CONTEXT_RE.search(window or ''))


def looks_like_invoice_id(candidate: str, window: str = '') -> bool:
    cand = (candidate or '').strip().strip('.:#')
    if not cand or cand.upper() in _INV_STOP:
        return False
    if looks_like_po(cand):
        return False
    if looks_like_bank_account(cand, window):
        return False
    if not re.search(r'\d', cand):
        return False
    if len(cand) < 4 or len(cand) > 24:
        return False
    if re.search(r'\d{1,2}[./-]\d{1,2}[./-]\d{2,4}', cand):
        return False
    if _POSTCODE_OUTWARD_RE.fullmatch(cand):
        return False
    if re.fullmatch(r'[A-Z]{2}\d{9,}', cand, re.I):  # VAT / EORI GB377109145
        return False
    if re.fullmatch(r'[A-Z]{1,10}[-/]?\d{2,}[A-Z0-9\-/]*', cand, re.I):
        return True
    if re.fullmatch(r'\d{4,16}', cand):
        if cand.startswith(('1323', '377109', '4470')):
            return False
        if re.fullmatch(r'(?:19|20)\d{2}', cand):
            return False
        return True
    return False


def _norm_invoice_id(cand: str) -> str:
    cand = (cand or '').strip().strip('.:#')
    return cand.upper() if re.search(r'[A-Za-z]', cand) else cand


def _token_key(tok: str) -> str:
    return tok.strip('.:#|,;').lower().rstrip('.')


def _is_letter_invoice_id(cand: str) -> bool:
    """INV00017249 / SIN134283 / AE88421 — not an 8-digit bank/account number."""
    cand = (cand or '').strip().strip('.:#')
    if looks_like_po(cand) or _POSTCODE_OUTWARD_RE.fullmatch(cand):
        return False
    return bool(re.fullmatch(r'[A-Z]{1,10}[-/]?\d{2,}[A-Z0-9\-/]*', cand, re.I))


def _id_after_invoice_no_label(span: str) -> Optional[str]:
    """Value for Invoice No:.

    1. Token immediately after the label (Invoice No: INV000 / 88421).
    2. After a header row (Date / Account / Your Ref), the first letter+digit
       ref — never the 8-digit Account No that follows those labels.
    """
    tokens = [
        t.strip('.:#|,;')
        for t in re.split(r'\s+', span[:180].strip())
        if t.strip('.:#|,;')
    ]
    i = 0
    saw_account = False
    while i < len(tokens) and _token_key(tokens[i]) in _COL_HEADERS:
        if _token_key(tokens[i]) in ('account', 'acc', 'a/c', 'a/c.'):
            saw_account = True
        i += 1
    if i < len(tokens) - 1 and re.fullmatch(r'[A-Za-z]{1,8}', tokens[i]) and tokens[i + 1].isdigit():
        glued = tokens[i] + tokens[i + 1]
        if _is_letter_invoice_id(glued) and looks_like_invoice_id(glued, span):
            return _norm_invoice_id(glued)
    if i < len(tokens):
        cand = tokens[i]
        if looks_like_invoice_id(cand, span) and not (
            saw_account and re.fullmatch(r'\d{8}', cand)
        ):
            return _norm_invoice_id(cand)
    for cand in tokens[i:]:
        if _token_key(cand) in _COL_HEADERS:
            continue
        if _is_letter_invoice_id(cand) and looks_like_invoice_id(cand, span):
            return _norm_invoice_id(cand)
    return None


def extract_invoice_number(text: str) -> Optional[str]:
    """The token next to the 'Invoice No:' field.

    Ignores Consignee / Invoice To, 'please quote invoice number', POs, and
    8-digit bank accounts in the Bank Details block.
    """
    if not text:
        return None

    masked = _PARTY_PHRASE_RE.sub(' ', text)
    flat = re.sub(r'[\s|]+', ' ', masked).strip()

    for m in _INV_LABEL_RE.finditer(flat):
        before = flat[max(0, m.start() - 48): m.start()]
        if _FALSE_LABEL_BEFORE_RE.search(before):
            continue
        found = _id_after_invoice_no_label(flat[m.end():])
        if found:
            return found
        prev = before.strip()
        if prev:
            last = prev.split()[-1].strip('.:#|,;')
            if looks_like_invoice_id(last, before):
                return _norm_invoice_id(last)
    return None


def invoice_number_from_pdf_words(words) -> Optional[str]:
    """Value to the right of, or just under, the topmost 'Invoice No' on the page."""
    if not words:
        return None
    texts = [(float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4])) for w in words]
    labels = []
    for i, (x0, y0, x1, y1, t) in enumerate(texts):
        combined = re.fullmatch(r'invoice\s*no\.?:?', t, re.I)
        nxt = texts[i + 1] if i + 1 < len(texts) else None
        if combined:
            no_box = (x0, y0, x1, y1, t)
            no_i = i
            inv_x0 = x0
        elif re.fullmatch(r'invoice', t, re.I) and nxt and re.fullmatch(
            r'(?:no\.?|nr\.?|n0|ne|num(?:ber)?|#):?', nxt[4], re.I
        ):
            if abs(nxt[1] - y0) > max(4.0, (y1 - y0) * 0.8):
                continue
            if nxt[0] - x1 > 80:
                continue
            nxt2 = texts[i + 2] if i + 2 < len(texts) else None
            if nxt2 and re.fullmatch(r'(?:to|date|due|address)', nxt2[4], re.I):
                continue
            no_box = nxt
            no_i = i + 1
            inv_x0 = x0
        else:
            continue
        if _FALSE_LABEL_BEFORE_RE.search(' '.join(w[4] for w in texts[max(0, i - 8): i])):
            continue
        labels.append((y0, no_i, no_box, inv_x0))
    if not labels:
        return None
    labels.sort(key=lambda r: (r[0], r[1]))
    _ly0, label_i, label, inv_x0 = labels[0]
    _lx0, ly0, lx1, ly1, _ = label
    line_tol = max(4.0, (ly1 - ly0) * 0.7)

    def _from_tokens(seq, saw_account=False):
        pending_prefix = None
        blob = ' '.join(seq)
        for t in seq:
            clean = t.strip('.:#|,;')
            if not clean:
                continue
            key = _token_key(clean)
            if key in ('account', 'acc', 'a/c', 'a/c.'):
                saw_account = True
                pending_prefix = None
                continue
            if key in _COL_HEADERS:
                pending_prefix = None
                continue
            if pending_prefix:
                glued = pending_prefix + clean
                if _is_letter_invoice_id(glued) and looks_like_invoice_id(glued, blob):
                    return _norm_invoice_id(glued)
                pending_prefix = None
            if saw_account and re.fullmatch(r'\d{8}', clean):
                continue
            if looks_like_invoice_id(clean, blob):
                if _is_letter_invoice_id(clean) or not saw_account:
                    return _norm_invoice_id(clean)
            if re.fullmatch(r'[A-Za-z]{1,8}', clean) and not looks_like_po(clean):
                pending_prefix = clean
        for t in seq:
            clean = t.strip('.:#|,;')
            if _is_letter_invoice_id(clean) and looks_like_invoice_id(clean, blob):
                return _norm_invoice_id(clean)
        return None

    right = sorted(
        ((x0, t) for x0, y0, _x1, _y1, t in texts[label_i + 1:]
         if abs(y0 - ly0) <= line_tol and x0 >= lx1 - 2),
        key=lambda r: r[0],
    )
    found = _from_tokens([t for _x, t in right])
    if found:
        return found
    col_right = lx1 + 110
    below = sorted(
        ((y0, x0, t) for x0, y0, _x1, _y1, t in texts[label_i + 1:]
         if y0 >= ly1 - 2 and y0 <= ly1 + (ly1 - ly0) * 5
         and x0 >= inv_x0 - 15 and x0 <= col_right),
        key=lambda r: (r[0], r[1]),
    )
    return _from_tokens([t for _y, _x, t in below])


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
