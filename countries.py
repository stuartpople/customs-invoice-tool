"""
Country data for customs declarations — sourced from the HMRC Trade Tariff API.
Fetched once per app start and cached to a local JSON file so the dropdown stays
current with HMRC's official country/territory list.
"""
import json
import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent / "countries_cache.json"
_API_URL = "https://www.trade-tariff.service.gov.uk/api/v2/geographical_areas"
_API_TIMEOUT = 10  # seconds

# ---------- static fallback (used if API + cache both unavailable) ----------
_FALLBACK_COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
    "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan",
    "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria",
    "Burkina Faso", "Burundi", "Cambodia", "Cameroon", "Canada", "Cape Verde",
    "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros",
    "Congo", "Costa Rica", "Croatia", "Cuba", "Cyprus", "Czech Republic",
    "Democratic Republic of the Congo", "Denmark", "Djibouti", "Dominica",
    "Dominican Republic", "East Timor", "Ecuador", "Egypt", "El Salvador",
    "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia", "Fiji",
    "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece",
    "Grenada", "Guatemala", "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras",
    "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland",
    "Israel", "Italy", "Ivory Coast", "Jamaica", "Japan", "Jordan", "Kazakhstan",
    "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon",
    "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg",
    "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands",
    "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia",
    "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea",
    "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama",
    "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal",
    "Qatar", "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe",
    "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore",
    "Sint Maarten (Dutch part)", "Slovakia", "Slovenia", "Solomon Islands", "Somalia",
    "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan",
    "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania",
    "Thailand", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey",
    "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu",
    "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
]


def _fetch_from_hmrc() -> tuple:
    """Fetch country names + ISO codes from HMRC geographical areas API.
    
    Returns:
        (countries_list, country_to_iso_dict)  or  (None, None) on failure.
    """
    try:
        resp = requests.get(_API_URL, timeout=_API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        countries = []
        code_map = {}
        for area in data.get("data", []):
            attrs = area.get("attributes", {})
            geo_id = attrs.get("geographical_area_id", "")
            desc = attrs.get("description", "")
            # 2-letter alpha IDs are individual countries/territories
            if len(geo_id) == 2 and geo_id.isalpha() and desc:
                countries.append(desc)
                code_map[desc] = geo_id

        countries.sort()

        # Persist to local cache
        try:
            _CACHE_PATH.write_text(json.dumps({
                "countries": countries,
                "country_to_iso": code_map,
            }, indent=2))
        except OSError:
            pass  # non-critical

        return countries, code_map
    except Exception as exc:
        logger.debug("HMRC geographical areas fetch failed: %s", exc)
        return None, None


def _load_cache() -> tuple:
    """Load from local JSON cache. Returns (list, dict) or (None, None)."""
    try:
        if _CACHE_PATH.exists():
            data = json.loads(_CACHE_PATH.read_text())
            return data["countries"], data["country_to_iso"]
    except Exception:
        pass
    return None, None


def _build_fallback_iso_map() -> dict:
    """Minimal name→ISO map for the static fallback list."""
    # Only the most common trading partners — the fallback list won't have
    # ISO codes for every entry; callers should handle missing keys gracefully.
    return {
        'China': 'CN', 'Germany': 'DE', 'United Kingdom': 'GB',
        'United States': 'US', 'France': 'FR', 'Italy': 'IT',
        'Spain': 'ES', 'India': 'IN', 'Japan': 'JP', 'South Korea': 'KR',
        'Azerbaijan': 'AZ', 'United Arab Emirates': 'AE', 'Canada': 'CA',
        'Australia': 'AU', 'Netherlands': 'NL', 'Belgium': 'BE',
        'Ireland': 'IE', 'Hong Kong': 'HK', 'Singapore': 'SG',
        'Taiwan': 'TW', 'Brazil': 'BR', 'Mexico': 'MX', 'Turkey': 'TR',
        'Saudi Arabia': 'SA', 'South Africa': 'ZA', 'Russia': 'RU',
        'Poland': 'PL', 'Sweden': 'SE', 'Norway': 'NO', 'Denmark': 'DK',
        'Switzerland': 'CH', 'Austria': 'AT', 'Portugal': 'PT',
        'Czech Republic': 'CZ', 'Romania': 'RO', 'Hungary': 'HU',
        'Greece': 'GR', 'Finland': 'FI', 'New Zealand': 'NZ',
        'Thailand': 'TH', 'Vietnam': 'VN', 'Malaysia': 'MY',
        'Indonesia': 'ID', 'Philippines': 'PH', 'Pakistan': 'PK',
        'Bangladesh': 'BD', 'Sri Lanka': 'LK', 'Egypt': 'EG',
        'Nigeria': 'NG', 'Kenya': 'KE', 'Ghana': 'GH',
        'Sint Maarten (Dutch part)': 'SX',
    }


# ---------------------------------------------------------------------------
# Module-level initialisation: cache → API → static fallback
# Tries the instant local cache first so Streamlit reruns stay fast.
# Only hits the HMRC API if no cache exists (first run or cache deleted).
# ---------------------------------------------------------------------------
COUNTRIES, COUNTRY_TO_ISO = _load_cache()
if COUNTRIES is None:
    COUNTRIES, COUNTRY_TO_ISO = _fetch_from_hmrc()
if COUNTRIES is None:
    COUNTRIES = list(_FALLBACK_COUNTRIES)
    COUNTRY_TO_ISO = _build_fallback_iso_map()

# Ensure "United Kingdom" is always present — HMRC's own API doesn't list
# the UK as a separate geographical area (it's the origin for the tariff),
# but users need it in the dropdown for consolidation / CofO purposes.
if "United Kingdom" not in COUNTRIES:
    COUNTRIES.append("United Kingdom")
    COUNTRIES.sort()
COUNTRY_TO_ISO.setdefault("United Kingdom", "GB")

# ISO 3166-1 alpha-3 → alpha-2 (invoices often show 3-letter codes; HMRC needs 2-letter)
_ALPHA3_PATH = Path(__file__).parent / "iso_alpha3_to_alpha2.json"
_ISO_ALPHA3_TO_ALPHA2: dict[str, str] = {}
try:
    if _ALPHA3_PATH.exists():
        _ISO_ALPHA3_TO_ALPHA2 = {
            k.upper(): v.upper()
            for k, v in json.loads(_ALPHA3_PATH.read_text()).items()
        }
except Exception:
    pass

# Case-insensitive country name → ISO alpha-2
_NAME_TO_ISO: dict[str, str] = {}
for _name, _code in COUNTRY_TO_ISO.items():
    _NAME_TO_ISO[_name.lower()] = _code
    _NAME_TO_ISO[_name.lower().replace(',', '')] = _code

# Common aliases not always in HMRC list
_NAME_ALIASES = {
    'uk': 'GB', 'u.k.': 'GB', 'great britain': 'GB', 'england': 'GB',
    'usa': 'US', 'u.s.a.': 'US', 'u.s.': 'US', 'america': 'US',
    'united states of america': 'US',
    'china': 'CN', 'prc': 'CN', "people's republic of china": 'CN',
    'south korea': 'KR', 'korea': 'KR', 'republic of korea': 'KR',
    'north korea': 'KP',
    'uae': 'AE', 'united arab emirates': 'AE',
    'holland': 'NL', 'the netherlands': 'NL',
    'ivory coast': 'CI', "cote d'ivoire": 'CI', "côte d'ivoire": 'CI',
    'czechia': 'CZ', 'czech republic': 'CZ',
    'viet nam': 'VN', 'vietnam': 'VN',
    'hong kong sar': 'HK', 'taiwan': 'TW',
    'sint maarten': 'SX',
}
_NAME_TO_ISO.update(_NAME_ALIASES)

# Valid HMRC-style 2-letter codes we accept as-is (from tariff geographical areas)
_VALID_ALPHA2 = set(COUNTRY_TO_ISO.values()) | {
    'GB', 'XI',  # XI used in some NI trade contexts
}


def looks_like_country_token(value: str | None) -> bool:
    """True when *value* is an ISO code or known country name — not free text.

    Unlike ``normalize_country_iso``, this does **not** treat the first two
    letters of an arbitrary product description as a country.
    """
    if value is None:
        return False
    raw = str(value).strip()
    if not raw or len(raw) > 40:
        return False
    if raw.lower() in ('null', 'none', 'n/a', 'na', '-', '—'):
        return False
    token = raw.upper().replace('.', '').replace('  ', ' ')
    if token in ('UK', 'U K'):
        return True
    if len(token) == 2 and token.isalpha():
        return token in _VALID_ALPHA2 or token == 'UK'
    if len(token) == 3 and token.isalpha():
        return token in _ISO_ALPHA3_TO_ALPHA2
    key = raw.lower().strip()
    if key in _NAME_TO_ISO or key.replace(',', '') in _NAME_TO_ISO:
        return True
    # Truncated Excel→PDF names e.g. "Myanma" for "Myanmar (Burma)"
    if len(key) >= 5:
        for name in _NAME_TO_ISO:
            primary = name.split('(')[0].strip()
            if len(primary) < 5:
                continue
            if primary.startswith(key) or key.startswith(primary[:5]):
                if abs(len(primary) - len(key)) <= 3:
                    return True
    return False


def normalize_country_iso(value: str | None) -> str:
    """
    Normalise country of origin / destination to ISO 3166-1 alpha-2 for HMRC/CDS.

    Handles: 3-letter (CHN→CN), 2-letter (uk→GB), full country names, and common aliases.
    """
    if value is None:
        return ''
    raw = str(value).strip()
    if not raw or raw.lower() in ('null', 'none', 'n/a', '-', '—'):
        return ''

    # Comma-separated (consolidated rows) — normalise each part
    if ',' in raw:
        parts = [normalize_country_iso(p.strip()) for p in raw.split(',')]
        parts = [p for p in parts if p]
        return ', '.join(dict.fromkeys(parts))  # preserve order, dedupe

    token = raw.upper().replace('.', '').replace('  ', ' ')

    if token in ('UK', 'U K'):
        return 'GB'

    # Already 2-letter
    if len(token) == 2 and token.isalpha():
        if token == 'UK':
            return 'GB'
        if token in _VALID_ALPHA2 or token.isalpha():
            return token

    # 3-letter (ISO alpha-3 on commercial invoices)
    if len(token) == 3 and token.isalpha():
        mapped = _ISO_ALPHA3_TO_ALPHA2.get(token)
        if mapped:
            return mapped
        # Unknown 3-letter — return as-is (user can fix) rather than truncate
        return token

    # Full name (any case)
    key = raw.lower().strip()
    if key in _NAME_TO_ISO:
        return _NAME_TO_ISO[key]
    key_compact = key.replace(',', '')
    if key_compact in _NAME_TO_ISO:
        return _NAME_TO_ISO[key_compact]

    # Title-case retry for ALL CAPS names
    titled = raw.title()
    if titled.lower() in _NAME_TO_ISO:
        return _NAME_TO_ISO[titled.lower()]

    return raw.upper()[:2] if len(raw) >= 2 and raw[:2].isalpha() else raw


def normalize_item_country_fields(item: dict) -> dict:
    """Apply normalize_country_iso to country_of_origin / country_origin on one line item."""
    if not isinstance(item, dict):
        return item
    for field in ('country_of_origin', 'country_origin'):
        if field in item and item[field]:
            item[field] = normalize_country_iso(item[field])
    return item


def normalize_items_country_fields(items: list) -> list:
    """Normalise country fields on every line item (in place)."""
    for it in items or []:
        normalize_item_country_fields(it)
    return items


# Common trading partners for quick selection
COMMON_COUNTRIES = [
    "United Kingdom", "United States", "China", "Germany", "France", "Netherlands",
    "Ireland", "Belgium", "Spain", "Italy", "India", "Japan", "Canada", "Australia",
    "Hong Kong", "Singapore", "South Korea", "Taiwan",
]


def format_country_option(name: str) -> str:
    """Label for the dropdown: ``United Kingdom (GB)`` so users can type either."""
    if not name or name == "---":
        return name
    iso = COUNTRY_TO_ISO.get(name) or ""
    if not iso:
        iso = normalize_country_iso(name)
    if iso and len(iso) == 2 and iso.isalpha():
        return f"{name} ({iso})"
    return name


def country_select_options(*, include_blank: bool = True) -> list:
    """Deduped origin/destination options: common partners, then A–Z with ISO codes.

    Common countries used to be listed again in the full HMRC list, which made
    Streamlit's search show duplicates (e.g. two ``China`` rows).
    """
    seen: set[str] = set()
    options: list[str] = []
    if include_blank:
        options.append("")
    for name in COMMON_COUNTRIES:
        if not name or name in seen:
            continue
        seen.add(name)
        options.append(format_country_option(name))
    options.append("---")
    for name in COUNTRIES:
        if not name or name in seen or name == "---":
            continue
        seen.add(name)
        options.append(format_country_option(name))
    return options


def parse_country_selection(selection: str | None) -> str:
    """Canonical country name from a selectbox value (plain name or ``Name (XX)``)."""
    if selection is None:
        return ""
    raw = str(selection).strip()
    if not raw or raw == "---":
        return ""
    m = re.match(r"^(.+)\s+\(([A-Za-z]{2})\)\s*$", raw)
    if m:
        name, code = m.group(1).strip(), m.group(2).upper()
        if name in COUNTRY_TO_ISO:
            return name
        for n, iso in COUNTRY_TO_ISO.items():
            if iso == code:
                return n
        return name
    if raw in COUNTRY_TO_ISO:
        return raw
    # Typed ISO only (rare — selectbox normally returns full label)
    if len(raw) == 2 and raw.isalpha():
        code = raw.upper()
        if code == "UK":
            return "United Kingdom"
        for n, iso in COUNTRY_TO_ISO.items():
            if iso == code:
                return n
    return raw


def selection_to_iso(selection: str | None) -> str | None:
    """ISO alpha-2 for a selectbox / metadata country value, or None."""
    name = parse_country_selection(selection)
    if not name:
        return None
    iso = COUNTRY_TO_ISO.get(name) or normalize_country_iso(name)
    if iso and len(iso) == 2 and iso.isalpha():
        return iso.upper()
    return None
