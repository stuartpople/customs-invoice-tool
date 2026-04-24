"""
GPT-4o powered invoice extractor.

Takes OCR text from any invoice and returns structured line items + metadata
by asking GPT-4o to reason about the layout.  Falls back gracefully when the
OpenAI key is not configured or the API call fails.

Returned item shape (matches existing regex-based output):
    {
        "commodity_code":  str,   # HS/HTS code, 8 or 10 digits
        "description":     str,
        "quantity":        float | None,
        "unit":            str,   # ea, kg, pcs, set …
        "unit_price":      float | None,
        "value":           float | None,  # line total
        "country_origin":  str | None,
        "net_weight":      float | None,  # kg
    }

Returned metadata shape (matches existing regex-based output):
    {
        "invoice_number":      str | None,
        "invoice_date":        str | None,
        "incoterm":            str | None,
        "currency":            str | None,
        "total_invoice_value": float | None,
        "total_gross_weight":  float | None,
        "total_net_weight":    float | None,
        "number_of_packages":  int | None,
        "package_type":        str | None,
    }
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

_OPENAI_AVAILABLE = False
try:
    from openai import OpenAI     # openai >= 1.0
    _OPENAI_AVAILABLE = True
except ImportError:
    pass


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a customs declaration specialist.  You will be given OCR text extracted
from a commercial invoice (possibly multi-page, possibly garbled by OCR errors).

Your task is to return ONLY a JSON object with two keys:
  "items"    – array of line items
  "metadata" – invoice-level fields

RULES:
- Do NOT invent data.  If a field is absent set it to null.
- Remove OCR artefacts from descriptions (stray characters, split words).
- HS/commodity codes are 6–10 digit numbers.  Return them as strings with no dots or spaces.
- All monetary values must be plain numbers (no currency symbols).
- net_weight is in KG.  Convert grams to KG if necessary.
- country_origin is the country where goods were manufactured (not destination).
- unit should be the unit of measure abbreviation (ea, kg, pcs, set, pr, m, l …).
- incoterm must be the 3-letter code only (EXW, FOB, CIF …).
- currency must be the 3-letter ISO code (GBP, USD, EUR …).

JSON schema:
{
  "items": [
    {
      "commodity_code":  "string or null",
      "description":     "string",
      "quantity":        number_or_null,
      "unit":            "string",
      "unit_price":      number_or_null,
      "value":           number_or_null,
      "country_origin":  "string or null",
      "net_weight":      number_or_null
    }
  ],
  "metadata": {
    "invoice_number":      "string or null",
    "invoice_date":        "string or null",
    "incoterm":            "string or null",
    "currency":            "string or null",
    "total_invoice_value": number_or_null,
    "total_gross_weight":  number_or_null,
    "total_net_weight":    number_or_null,
    "number_of_packages":  integer_or_null,
    "package_type":        "string or null"
  }
}

Return ONLY the JSON — no markdown, no explanation, no preamble.
"""

_MAX_OCR_CHARS = 60_000   # GPT-4o context is 128k tokens; 60k chars ≈ 15k tokens (safe)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_with_llm(
    ocr_text: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Tuple[List[Dict], Dict]:
    """
    Send OCR text to GPT-4o and return (items, metadata).

    Args:
        ocr_text:  Combined OCR text from all invoice pages.
        api_key:   OpenAI API key.
        model:     OpenAI model name (default "gpt-4o").

    Returns:
        Tuple of (items list, metadata dict).  Both are empty/null-filled on failure.

    Raises:
        RuntimeError: if openai package is not installed.
        Exception:    any OpenAI API error is re-raised so the caller can fall back.
    """
    if not _OPENAI_AVAILABLE:
        raise RuntimeError(
            "openai package is not installed. Add 'openai>=1.0' to requirements.txt."
        )

    # Truncate to stay within context limits
    text = ocr_text[:_MAX_OCR_CHARS]
    if len(ocr_text) > _MAX_OCR_CHARS:
        text += f"\n\n[... {len(ocr_text) - _MAX_OCR_CHARS} characters truncated ...]"

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        temperature=0,           # deterministic output
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": text},
        ],
    )

    raw = response.choices[0].message.content
    data = _parse_response(raw)

    items    = _normalise_items(data.get("items", []))
    metadata = _normalise_metadata(data.get("metadata", {}))
    return items, metadata


def is_available() -> bool:
    """Return True if the openai package is installed."""
    return _OPENAI_AVAILABLE


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_response(raw: str) -> Dict:
    """Parse the JSON response, stripping any accidental markdown fences."""
    # Strip ```json … ``` if model forgot to follow instructions
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _normalise_items(raw_items: list) -> List[Dict]:
    """Coerce each item to the expected schema."""
    result = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        result.append({
            "commodity_code": _str_or_none(it.get("commodity_code")),
            "description":    str(it.get("description") or "").strip(),
            "quantity":       _float_or_none(it.get("quantity")),
            "unit":           str(it.get("unit") or "pcs").lower().strip(),
            "unit_price":     _float_or_none(it.get("unit_price")),
            "value":          _float_or_none(it.get("value")),
            "country_origin": _str_or_none(it.get("country_origin")),
            "net_weight":     _float_or_none(it.get("net_weight")),
        })
    return result


def _normalise_metadata(raw: dict) -> Dict:
    """Coerce metadata to the expected schema."""
    return {
        "invoice_number":      _str_or_none(raw.get("invoice_number")),
        "invoice_date":        _str_or_none(raw.get("invoice_date")),
        "incoterm":            _str_or_none(raw.get("incoterm")),
        "currency":            _str_or_none(raw.get("currency")),
        "total_invoice_value": _float_or_none(raw.get("total_invoice_value")),
        "total_gross_weight":  _float_or_none(raw.get("total_gross_weight")),
        "total_net_weight":    _float_or_none(raw.get("total_net_weight")),
        "number_of_packages":  _int_or_none(raw.get("number_of_packages")),
        "package_type":        _str_or_none(raw.get("package_type")),
    }


def _str_or_none(v) -> Optional[str]:
    if v is None or str(v).strip().lower() in ("", "null", "none"):
        return None
    return str(v).strip()


def _float_or_none(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int_or_none(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None
