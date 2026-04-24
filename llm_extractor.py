"""
AI-powered invoice extractor.

Supports two providers (tried in order):
  1. Google Gemini Flash  — FREE tier: 1,500 requests/day, no credit card needed.
                            Key from: https://aistudio.google.com/apikey
                            Secret name: GOOGLE_API_KEY
  2. OpenAI GPT-4o-mini   — ~£0.001 per invoice (pay-as-you-go).
                            Key from: https://platform.openai.com/api-keys
                            Secret name: OPENAI_API_KEY

The caller (line_item_parser.py) supplies whichever key(s) are configured.
Falls back gracefully to regex parsing if neither key is available or both fail.

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
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    pass

_GEMINI_AVAILABLE = False
try:
    import google.generativeai as genai
    _GEMINI_AVAILABLE = True
except ImportError:
    pass


# ── Shared prompt ─────────────────────────────────────────────────────────────

# Prompt tuning for robustness to noisy OCR
_SYSTEM_PROMPT = (
    "You are an expert at extracting structured data from messy, scanned, or OCR'd invoices. "
    "The input may have misaligned columns, extra whitespace, or character errors. "
    "Infer the correct table structure and fields even if the text is noisy. "
    'You MUST return ONLY a valid JSON object with exactly this structure: {"items": [...], "metadata": {...}}. '
    "Each object in 'items' must have these fields: commodity_code (HS/tariff code as string), "
    "description (product name/description), quantity (number), unit (ea/pcs/kg/set etc), "
    "unit_price (price per unit), value (line total), country_origin (country of manufacture), net_weight (kg). "
    "The 'metadata' object must have: invoice_number, invoice_date, incoterm, currency, "
    "total_invoice_value, total_gross_weight, total_net_weight, number_of_packages, package_type. "
    "If a value is missing or ambiguous, use null. Do not hallucinate. "
    "Be robust to OCR errors — infer values even if text is slightly garbled. "
    "Extract ALL line items you can find, even if the table is misaligned."
)

_MAX_OCR_CHARS = 60_000   # 60k chars ≈ 15k tokens — well within both providers' limits


# ── Public API ────────────────────────────────────────────────────────────────

def extract_with_gemini(
    ocr_text: str,
    api_key: str,
    model: str = "gemini-1.5-flash",
) -> Tuple[List[Dict], Dict]:
    """
    Extract invoice data using Google Gemini Flash (free tier).

    Args:
        ocr_text: Combined OCR text from all invoice pages.
        api_key:  Google AI Studio API key.
        model:    Gemini model name (default "gemini-1.5-flash").

    Raises:
        RuntimeError: if google-generativeai package is not installed.
    """
    if not _GEMINI_AVAILABLE:
        raise RuntimeError(
            "google-generativeai package is not installed. "
            "Add 'google-generativeai>=0.5' to requirements.txt."
        )

    text = _truncate(ocr_text)
    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(
        model_name=model,
        generation_config=genai.types.GenerationConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
        system_instruction=_SYSTEM_PROMPT,
    )
    response = gemini_model.generate_content(text)
    data = _parse_response(response.text)
    return _normalise_items(data.get("items", [])), _normalise_metadata(data.get("metadata", {}))


def extract_with_llm(
    ocr_text: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Tuple[List[Dict], Dict]:
    """
    Extract invoice data using OpenAI (gpt-4o-mini by default).

    Args:
        ocr_text: Combined OCR text from all invoice pages.
        api_key:  OpenAI API key.
        model:    OpenAI model name.

    Raises:
        RuntimeError: if openai package is not installed.
    """
    if not _OPENAI_AVAILABLE:
        raise RuntimeError(
            "openai package is not installed. Add 'openai>=1.0' to requirements.txt."
        )

    text = _truncate(ocr_text)
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": text},
        ],
    )
    data = _parse_response(response.choices[0].message.content)
    return _normalise_items(data.get("items", [])), _normalise_metadata(data.get("metadata", {}))


def gemini_available() -> bool:
    """Return True if the google-generativeai package is installed."""
    return _GEMINI_AVAILABLE


def openai_available() -> bool:
    """Return True if the openai package is installed."""
    return _OPENAI_AVAILABLE


# ── Internal helpers ──────────────────────────────────────────────────────────

def _truncate(text: str) -> str:
    if len(text) > _MAX_OCR_CHARS:
        return text[:_MAX_OCR_CHARS] + f"\n\n[... {len(text) - _MAX_OCR_CHARS} characters truncated ...]"
    return text


def _parse_response(raw: str) -> Dict:
    """Parse JSON response, stripping accidental markdown fences."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _normalise_items(raw_items: list) -> List[Dict]:
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


