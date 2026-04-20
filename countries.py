"""
Country data for customs declarations — sourced from the HMRC Trade Tariff API.
Fetched once per app start and cached to a local JSON file so the dropdown stays
current with HMRC's official country/territory list.
"""
import json
import logging
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

# Common trading partners for quick selection
COMMON_COUNTRIES = [
    "United Kingdom", "United States", "China", "Germany", "France", "Netherlands",
    "Ireland", "Belgium", "Spain", "Italy", "India", "Japan", "Canada", "Australia",
    "Hong Kong", "Singapore", "South Korea", "Taiwan",
]
