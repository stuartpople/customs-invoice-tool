"""
HMRC Trade Tariff API integration module
API Documentation: https://www.trade-tariff.service.gov.uk/api/v2/
"""
import re as _re
import requests
from typing import Dict, Optional, List
import time


# Phrases in requirement text that indicate a non-restrictive / exemption code
_EXEMPTION_PHRASES = [
    'not required',
    'not subject to',
    'not concerned',
    'not listed',
    'do not apply',
    'does not apply',
    'not applicable',
    'exemption applies',
    'does not concern',
    'not controlled',
    'not restricted',
    'goods not',
]


def _pick_preferred_codes(groups: List[Dict]) -> Dict[str, str]:
    """
    From each document-code group pick the non-restrictive / exemption code.

    Heuristics (applied in order):
    1. If only one code in the group → use it.
    2. If a code's requirement text contains an exemption phrase → prefer it.
    3. Otherwise take the last code in the group (per user's observation that
       the exemption code is normally listed last).

    Returns:
        Dict mapping code → requirement for the selected codes (deduplicated).
    """
    selected: Dict[str, str] = {}
    for grp in groups:
        codes = grp.get('codes', [])
        if not codes:
            continue

        if len(codes) == 1:
            selected[codes[0]['code']] = codes[0]['requirement']
            continue

        # Look for an exemption code by requirement text
        exemption = None
        for c in codes:
            req_lower = c['requirement'].lower()
            if any(phrase in req_lower for phrase in _EXEMPTION_PHRASES):
                exemption = c

        if exemption:
            selected[exemption['code']] = exemption['requirement']
        else:
            # Second heuristic: prefer Y-prefix codes — in UK tariff these are
            # the standard "not required" / exemption declaration codes
            # (e.g. Y900, 9Y10, 9Y07 all mean the goods are exempt from the
            # associated restriction).
            y_code = None
            for c in codes:
                code_upper = c['code'].upper()
                if code_upper.startswith('Y') or (len(code_upper) >= 2 and code_upper[1] == 'Y'):
                    y_code = c
                    break
            if y_code:
                selected[y_code['code']] = y_code['requirement']
            else:
                # Fall back to last code in the group
                last = codes[-1]
                selected[last['code']] = last['requirement']

    return selected


class HMRCTariffAPI:
    """
    Interface to HMRC Trade Tariff API for commodity code lookups
    """
    # Class-level cache shared across all instances to avoid redundant API calls
    _commodity_cache: Dict[str, Optional[Dict]] = {}
    _heading_cache: Dict[str, Optional[Dict]] = {}
    _validation_cache: Dict[str, Dict] = {}
    
    def __init__(self, base_url: str = "https://www.trade-tariff.service.gov.uk"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'CustomsInvoiceTool/1.0'
        })

    # ------------------------------------------------------------------
    # Commodity code validation
    # ------------------------------------------------------------------
    def validate_commodity_code(self, code: str, direction: str = "export") -> Dict:
        """
        Validate that a commodity code exists in the UK Trade Tariff.

        For **export**: an 8-digit CN code is valid if *any* TARIC (10-digit)
        variant underneath it is a declarable leaf.  The Excel output keeps 8
        digits; the API will expand to 10 when doing doc-code look-ups.

        For **import**: the full 10-digit TARIC code must itself be a
        declarable leaf.

        Returns a dict:
            {
                "valid": bool,
                "code": str,           # the cleaned code checked
                "resolved_code": str,  # 10-digit code that matched (if valid)
                "description": str,    # tariff description (if valid)
                "error": str,          # human-readable message (if invalid)
            }
        """
        clean = code.replace(' ', '').replace('-', '').replace('.', '')
        if not _re.match(r'^\d{6,10}$', clean):
            return {"valid": False, "code": code,
                    "error": f"'{code}' is not a valid format (need 6-10 digits)"}

        is_export = direction.lower() == "export"

        # Step 0: For exports, strip any code longer than 8 digits to CN8.
        # Invoices from some suppliers carry 10-digit TARIC codes; the UK
        # export tariff only uses 8-digit CN codes so the extra digits are
        # meaningless and cause false "not declarable" errors.
        if is_export and len(clean) > 8:
            clean = clean[:8]

        cache_key = f"{clean}_{direction.lower()}"
        if cache_key in self._validation_cache:
            return self._validation_cache[cache_key]

        # Pad to 10 digits
        padded = clean.ljust(10, '0')

        # 1) Try exact padded code
        result = self._try_commodity(padded)
        if result and result.get('leaf', True):
            out = {"valid": True, "code": clean, "resolved_code": padded,
                   "description": result.get('description', '')}
            self._validation_cache[cache_key] = out
            return out

        # 2) For ≤8-digit codes, try common TARIC suffixes (digits 9-10)
        if len(clean) <= 8:
            base8 = clean[:8].ljust(8, '0')
            for suffix in ['00', '10', '20', '30', '40', '50',
                           '60', '70', '80', '90', '91', '99']:
                variant = base8 + suffix
                if variant == padded:
                    continue
                res = self._try_commodity(variant)
                if res and res.get('leaf', True):
                    if is_export:
                        # Export only needs valid 8-digit CN code.
                        # A TARIC variant existing proves the CN8 is real.
                        out = {"valid": True, "code": clean,
                               "resolved_code": variant,
                               "description": res.get('description', '')}
                    else:
                        # Import needs exact 10-digit leaf
                        out = {"valid": False, "code": clean,
                               "resolved_code": variant,
                               "description": res.get('description', ''),
                               "error": (f"{code} is not a declarable 10-digit code.  "
                                         f"Did you mean {variant} "
                                         f"({res.get('description', '')[:60]})?")}
                    self._validation_cache[cache_key] = out
                    return out

        # 3) For 10-digit import codes ending in '00' (padded 8-digit),
        #    also try TARIC suffixes
        if len(clean) == 10 and clean.endswith('00') and not is_export:
            base8 = clean[:8]
            for suffix in ['10', '20', '30', '40', '50',
                           '60', '70', '80', '90', '91', '99']:
                variant = base8 + suffix
                res = self._try_commodity(variant)
                if res and res.get('leaf', True):
                    out = {"valid": False, "code": clean,
                           "resolved_code": variant,
                           "description": res.get('description', ''),
                           "error": (f"{code} is not a declarable code.  "
                                     f"Did you mean {variant} "
                                     f"({res.get('description', '')[:60]})?")}
                    self._validation_cache[cache_key] = out
                    return out

        # 4) Code doesn't resolve via TARIC suffixes — explore the heading
        #    to find valid leaf codes that share the longest prefix with the
        #    input code.  This catches cases like "82075000" (CN6 820750
        #    zero-padded to 8 digits) where the real CN8 codes are
        #    82075010, 82075060, etc.
        #    For obsolete codes, also search the entire heading for replacements.
        heading = clean[:4]
        heading_desc = ''
        candidates: List[Dict] = []
        try:
            r = self.session.get(
                f"{self.base_url}/uk/api/headings/{heading}", timeout=10)
            if r.status_code == 200:
                hdata = r.json()
                hd = hdata.get('data', {}).get('attributes', {})
                heading_desc = hd.get('description_plain',
                                      hd.get('description', ''))
                # Gather all leaf commodities under this heading
                # First try to match by 6-digit prefix (for similar codes)
                prefix6 = clean[:6]
                prefix4 = clean[:4]
                
                for inc in hdata.get('included', []):
                    if inc.get('type') != 'commodity':
                        continue
                    attrs = inc.get('attributes', {})
                    if not attrs.get('leaf', False):
                        continue
                    ccode = attrs.get('goods_nomenclature_item_id', '')
                    # Try exact prefix6 match first (e.g., 82075010 for 82075000)
                    if ccode.startswith(prefix6):
                        cdesc = (attrs.get('description_plain') or
                                 attrs.get('formatted_description', '')).strip()
                        candidates.append({'code': ccode, 'description': cdesc})
                
                # If no exact prefix6 match found (likely obsolete), search entire heading
                # This catches cases like 8517700 → 8517710 or 8517790
                if not candidates:
                    for inc in hdata.get('included', []):
                        if inc.get('type') != 'commodity':
                            continue
                        attrs = inc.get('attributes', {})
                        if not attrs.get('leaf', False):
                            continue
                        ccode = attrs.get('goods_nomenclature_item_id', '')
                        # Only include codes in same heading (first 4 digits match)
                        if ccode.startswith(prefix4) and not ccode.startswith(prefix6):
                            cdesc = (attrs.get('description_plain') or
                                     attrs.get('formatted_description', '')).strip()
                            candidates.append({'code': ccode, 'description': cdesc})
        except requests.RequestException:
            pass

        if candidates:
            # Deduplicate by code
            seen = set()
            unique = []
            for c in candidates:
                if c['code'] not in seen:
                    seen.add(c['code'])
                    unique.append(c)
            candidates = unique

            if len(candidates) == 1:
                # Single leaf under this subheading → auto-correct
                rc = candidates[0]['code']
                out = {"valid": False, "code": clean,
                       "resolved_code": rc,
                       "description": candidates[0]['description'],
                       "error": (f"{code} is not a declarable code.  "
                                 f"Auto-resolved to {rc} "
                                 f"({candidates[0]['description'][:60]})")}
            else:
                # Multiple leaves — list up to 5 as suggestions
                suggestions = "; ".join(
                    f"{c['code'][:8]} ({c['description'][:40]})"
                    for c in candidates[:5]
                )
                extra = f" +{len(candidates)-5} more" if len(candidates) > 5 else ""
                out = {"valid": False, "code": clean,
                       "candidates": candidates,
                       "error": (f"{code} is not a declarable code. "
                                 f"Possible codes: {suggestions}{extra}")}
        else:
            ctx = f" (heading {heading}: {heading_desc})" if heading_desc else ""
            out = {"valid": False, "code": clean,
                   "error": f"{code} is not a valid commodity code{ctx}"}

        self._validation_cache[cache_key] = out
        return out

    def validate_commodity_codes(self, codes: List[str],
                                 direction: str = "export") -> Dict[str, Dict]:
        """Batch-validate a list of commodity codes. Returns {code: result}."""
        results: Dict[str, Dict] = {}
        for c in set(codes):
            if c and _re.match(r'^\d{6,10}$', c.replace(' ', '').replace('-', '').replace('.', '')):
                results[c] = self.validate_commodity_code(c, direction=direction)
        return results

    def _try_commodity(self, ten_digit_code: str) -> Optional[Dict]:
        """Hit the commodities endpoint; return attrs dict or None on 404."""
        try:
            r = self.session.get(
                f"{self.base_url}/uk/api/commodities/{ten_digit_code}",
                timeout=10)
            if r.status_code == 200:
                attrs = r.json().get('data', {}).get('attributes', {})
                return {
                    'description': (attrs.get('description_plain') or
                                    attrs.get('formatted_description', '')).strip(),
                    'leaf': attrs.get('leaf', True),
                    'code': attrs.get('goods_nomenclature_item_id', ten_digit_code),
                }
        except requests.RequestException:
            pass
        return None
    
    def get_commodity_details(self, commodity_code: str, country: str = "GB", direction: str = "import", destination_country: str = None, export_only: bool = False) -> Optional[Dict]:
        """
        Get detailed information about a commodity code from HMRC API.
        
        Args:
            commodity_code: 8-10 digit commodity code
            country: GB (default) or XI (Northern Ireland)
            direction: "import" or "export" - affects which measures are returned
            destination_country: ISO country code for filtering measures (e.g., "CN" for China)
            export_only: If True, only include export-related measures (Export control, Export authorization)
            
        Returns:
            Dictionary with commodity details or None if not found
        """
        # Clean commodity code (remove spaces, dashes)
        clean_code = commodity_code.replace(' ', '').replace('-', '')
        
        # Check cache first
        cache_key = f"{clean_code}_{direction}_{destination_country}_{export_only}"
        if cache_key in self._commodity_cache:
            return self._commodity_cache[cache_key]
        
        # Try multiple variations if code not found
        code_variants = []
        
        # Special handling for imports: 10-digit codes ending in '00' are likely padded 8-digit codes
        # Try TARIC variants instead of using the padded code directly
        is_padded_8_digit = (len(clean_code) == 10 and clean_code.endswith('00') and 
                            direction.lower() == 'import')
        
        if len(clean_code) < 10:
            if len(clean_code) == 8:
                # For 8-digit codes, try common TARIC suffixes first (these are most specific)
                # 99 = "Other" catch-all, 90/91/80/10 = common specific categories
                for suffix in ['99', '91', '90', '80', '10', '00']:
                    code_variants.append(clean_code + suffix)
                # Then try broader levels (heading, chapter)
                code_variants.append(clean_code[:6] + '0000')  # 6-digit heading
                code_variants.append(clean_code[:4] + '000000')  # 4-digit chapter
            else:
                # For other lengths, just pad with zeros
                code_variants.append(clean_code.ljust(10, '0'))
        elif is_padded_8_digit:
            # This is a padded 8-digit code for import - try TARIC variants
            base_8_digit = clean_code[:8]
            for suffix in ['99', '91', '90', '80', '10', '00']:
                code_variants.append(base_8_digit + suffix)
            # Fallback to broader levels
            code_variants.append(base_8_digit[:6] + '0000')  # 6-digit heading
            code_variants.append(base_8_digit[:4] + '000000')  # 4-digit chapter
        else:
            code_variants.append(clean_code)
        
        last_error = None
        for variant_code in code_variants:
            try:
                # Use UK endpoint (GB tariff)
                url = f"{self.base_url}/uk/api/commodities/{variant_code}"
                params = {}
                
                response = self.session.get(url, params=params, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    result = self._parse_commodity_response(data, direction, destination_country, export_only)
                    self._commodity_cache[cache_key] = result
                    return result
                elif response.status_code == 404:
                    last_error = {"error": f"Code {commodity_code} not found in HMRC database (tried {variant_code})"}
                    continue  # Try next variant
                else:
                    last_error = {"error": f"API error: HTTP {response.status_code}"}
                    continue
            except requests.RequestException as e:
                last_error = {"error": f"Network error: {str(e)}"}
                continue
        
        # If we get here, none of the variants worked
        result = last_error or {"error": f"Code {commodity_code} not found"}
        self._commodity_cache[cache_key] = result
        return result
    
    def _extract_duty_from_components(self, measure_components_data, component_lookup, duty_expression_lookup):
        """
        Extract human-readable duty rate from measure components.
        
        Args:
            measure_components_data: List of component references from measure.relationships.measure_components.data
            component_lookup: Dict mapping component IDs to component objects
            duty_expression_lookup: Dict mapping duty_expression IDs to expression objects
            
        Returns:
            String representation of the duty rate, or None if not found
        """
        if not measure_components_data:
            return None
            
        duty_parts = []
        for comp_ref in measure_components_data:
            comp_id = comp_ref.get('id')
            component = component_lookup.get(comp_id, {})
            comp_attrs = component.get('attributes', {})
            comp_rels = component.get('relationships', {})
            
            # Get duty amount and measurement unit
            duty_amount = comp_attrs.get('duty_amount')
            
            # Get duty expression (e.g., "% or €/100 kg")
            duty_expr_ref = comp_rels.get('duty_expression', {}).get('data', {})
            duty_expr_id = duty_expr_ref.get('id')
            duty_expr_obj = duty_expression_lookup.get(duty_expr_id, {})
            duty_expr_attrs = duty_expr_obj.get('attributes', {})
            duty_expr_base = duty_expr_attrs.get('base', '')
            
            # Combine amount + expression
            if duty_amount is not None and duty_expr_base:
                duty_parts.append(f"{duty_amount}{duty_expr_base}")
            elif duty_amount is not None:
                duty_parts.append(str(duty_amount))
            elif duty_expr_base:
                duty_parts.append(duty_expr_base)
        
        if duty_parts:
            return " + ".join(duty_parts)
        return None
    
    def _parse_commodity_response(self, data: Dict, direction: str = "import", destination_country: str = None, export_only: bool = False) -> Dict:
        """
        Parse HMRC API response into simplified structure with CDS-relevant data.
        
        Args:
            data: Raw API response
            direction: "import" or "export" - filters relevant measures
            destination_country: ISO country code for filtering (e.g., "CN")
            
        Returns:
            Simplified commodity information with CDS data
        """
        commodity_data = data.get('data', {}).get('attributes', {})
        
        result = {
            "commodity_code": commodity_data.get('goods_nomenclature_item_id'),
            "description": commodity_data.get('formatted_description', '').strip(),
            "supplementary_units": None,
            "vat_rate": None,
            "third_country_duty": None,
            "preferential_duty": {},
            "suspensions": [],
            "quotas": [],
            "anti_dumping": [],
            "prohibitions": [],
            "conditions": [],
            "additional_codes": [],
            "measures": [],
            "import_measures": [],
            "export_measures": [],
            "country_specific_measures": [],
            "direction": direction,
            "destination_country": destination_country,
            # Debug tracking
            "_debug_total_measures": 0,
            "_debug_filtered_count": 0,
            "_debug_all_measures": [],
            "_debug_direction_checks": [],
            "_debug_country_checks": []
        }
        
        # Extract measures and build lookups for related data
        measures = data.get('included', [])
        document_codes = {}
        geo_lookup = {i.get('id'): i for i in measures if i.get('type') == 'geographical_area'}
        additional_code_lookup = {i.get('id'): i for i in measures if i.get('type') == 'additional_code'}
        measure_condition_lookup = {i.get('id'): i for i in measures if i.get('type') == 'measure_condition'}
        measure_type_lookup = {i.get('id'): i for i in measures if i.get('type') == 'measure_type'}
        measure_component_lookup = {i.get('id'): i for i in measures if i.get('type') == 'measure_component'}
        duty_expression_lookup = {i.get('id'): i for i in measures if i.get('type') == 'duty_expression'}
        
        # First pass: Process measures and link document codes and conditions to them
        measure_docs = {}  # Maps measure_id -> {geo_code, doc_codes[], conditions[]}
        
        for measure in measures:
            if measure.get('type') == 'measure':
                attrs = measure.get('attributes', {})
                rels = measure.get('relationships', {})
                measure_id = measure.get('id')
                
                # Get geographical area for this measure
                geo_data = rels.get('geographical_area', {}).get('data', {})
                geo_id = geo_data.get('id', '')
                geo_area = geo_lookup.get(geo_id, {})
                geo_attrs = geo_area.get('attributes', {})
                geo_country_code = geo_attrs.get('id', '')
                
                # Get measure conditions (which contain document codes AND condition text)
                condition_refs = rels.get('measure_conditions', {}).get('data', [])
                measure_doc_codes = []
                measure_conditions = []
                
                for cond_ref in condition_refs:
                    cond_id = cond_ref.get('id')
                    condition = measure_condition_lookup.get(cond_id)
                    if condition:
                        cond_attrs = condition.get('attributes', {})
                        doc_code = cond_attrs.get('document_code', '').strip()
                        requirement = cond_attrs.get('requirement') or cond_attrs.get('action', '')
                        condition_text = cond_attrs.get('condition', '')
                        
                        # Collect condition text
                        if condition_text and condition_text not in measure_conditions:
                            measure_conditions.append(condition_text)
                        
                        # Use document_code if available, otherwise use condition_code for document-type conditions
                        if doc_code:
                            measure_doc_codes.append({
                                'code': doc_code,
                                'requirement': requirement or 'Required'
                            })
                        elif cond_attrs.get('condition_code') in ['B', 'C'] and cond_attrs.get('measure_condition_class') == 'document':
                            # Condition requires a document but no specific code given - use condition description
                            if condition_text:
                                measure_doc_codes.append({
                                    'code': cond_attrs.get('condition_code'),
                                    'requirement': requirement or condition_text
                                })
                
                if measure_doc_codes or measure_conditions:
                    measure_docs[measure_id] = {
                        'geo_code': geo_country_code,
                        'doc_codes': measure_doc_codes,
                        'conditions': measure_conditions
                    }
        
        # Second pass: Process measures with country filtering
        for measure in measures:
            if measure.get('type') == 'measure':
                result['_debug_total_measures'] += 1
                attrs = measure.get('attributes', {})
                rels = measure.get('relationships', {})
                measure_id = measure.get('id')
                
                # Get measure type
                measure_type_rel = rels.get('measure_type', {}).get('data', {})
                measure_type_id = measure_type_rel.get('id', '')
                measure_type_obj = measure_type_lookup.get(measure_type_id, {})
                measure_type = measure_type_obj.get('attributes', {}).get('description', '')
                
                # Get duty expression for debug
                duty_expr = attrs.get('duty_expression', {})
                if isinstance(duty_expr, dict):
                    # Try formatted_base first, then base, then verbatim
                    duty_text = (
                        duty_expr.get('formatted_base', '') or 
                        duty_expr.get('base', '') or
                        duty_expr.get('verbatim', '') or
                        ''
                    )
                else:
                    duty_text = str(duty_expr) if duty_expr else ''
                
                # If still empty or masked, try measure_components
                if not duty_text or duty_text == '****':
                    measure_components = rels.get('measure_components', {}).get('data', [])
                    if measure_components:
                        # Extract actual duty from components
                        extracted_duty = self._extract_duty_from_components(
                            measure_components, 
                            measure_component_lookup, 
                            duty_expression_lookup
                        )
                        if extracted_duty:
                            duty_text = extracted_duty
                        else:
                            duty_text = f"See {len(measure_components)} component(s)"
                
                # Get geographical area
                geo_data = rels.get('geographical_area', {}).get('data', {})
                geo_id = geo_data.get('id', '')
                geo_area = geo_lookup.get(geo_id, {})
                geo_attrs = geo_area.get('attributes', {})
                geo_description = geo_attrs.get('description', '')
                
                # Debug: Collect ALL measures BEFORE filtering
                result['_debug_all_measures'].append({
                    'type': measure_type,
                    'type_id': measure_type_id,
                    'duty': duty_text,
                    'geo': geo_description,
                    'has_import': 'import' in measure_type.lower(),
                    'has_export': 'export' in measure_type.lower()
                })
                
                # Check if this measure matches the requested direction
                is_import_measure = 'import' in measure_type.lower() if measure_type else False
                is_export_measure = 'export' in measure_type.lower() if measure_type else False
                direction_matches = False
                
                if direction.lower() == "import":
                    direction_matches = is_import_measure or (not is_import_measure and not is_export_measure)
                elif direction.lower() == "export":
                    direction_matches = is_export_measure or (not is_import_measure and not is_export_measure)
                else:
                    direction_matches = True
                
                # Debug: Track direction filtering decisions
                result['_debug_direction_checks'].append({
                    'measure': measure_type[:40] if measure_type else 'NO TYPE',
                    'direction_requested': direction,
                    'has_import': is_import_measure,
                    'has_export': is_export_measure,
                    'matches': direction_matches,
                    'export_only': export_only
                })
                
                # Filter by export_only if specified
                if export_only:
                    if not is_export_measure:
                        result['_debug_filtered_count'] += 1
                        continue  # Skip non-export measures
                
                # Skip measures that don't match the requested direction
                if not direction_matches:
                    result['_debug_filtered_count'] += 1
                    continue
                if not direction_matches:
                    result['_debug_filtered_count'] += 1
                    continue
                
                # Get duty expression - try multiple fields
                duty_expr = attrs.get('duty_expression', {})
                if isinstance(duty_expr, dict):
                    # Try formatted_base first, then base, then verbatim
                    duty_text = (
                        duty_expr.get('formatted_base', '') or 
                        duty_expr.get('base', '') or
                        duty_expr.get('verbatim', '') or
                        ''
                    )
                else:
                    duty_text = str(duty_expr) if duty_expr else ''
                
                # If still empty or masked, try measure_components
                if not duty_text or duty_text == '****':
                    measure_components = rels.get('measure_components', {}).get('data', [])
                    if measure_components:
                        # Extract actual duty from components
                        extracted_duty = self._extract_duty_from_components(
                            measure_components, 
                            measure_component_lookup, 
                            duty_expression_lookup
                        )
                        if extracted_duty:
                            duty_text = extracted_duty
                        else:
                            duty_text = f"See {len(measure_components)} component(s)"
                
                # Get geographical area for this measure
                geo_data = rels.get('geographical_area', {}).get('data', {})
                geo_id = geo_data.get('id', '')
                geo_area = geo_lookup.get(geo_id, {})
                geo_attrs = geo_area.get('attributes', {})
                geo_description = geo_attrs.get('description', '')
                geo_country_code = geo_attrs.get('id', '')
                
                # Filter by destination/origin country if specified
                if destination_country:
                    geo_desc_lower = geo_description.lower() if geo_description else ''
                    measure_type_lower = measure_type.lower() if measure_type else ''
                    
                    # VAT and excise measures apply to ALL UK imports regardless of origin
                    is_universal_measure = (
                        'vat' in measure_type_lower or
                        'value added tax' in measure_type_lower or
                        'excise' in measure_type_lower or
                        'vat or excise' in geo_desc_lower or
                        'areas subject to' in geo_desc_lower
                    )
                    
                    is_all_countries = (
                        str(geo_country_code) == '1011' or 
                        'erga omnes' in geo_desc_lower or
                        geo_desc_lower == '' or
                        'all countries' in geo_desc_lower or
                        is_universal_measure
                    )
                    
                    # FIXED: Only use country CODE comparison, not substring matching!
                    # Substring matching was catching "US" in "RUSSIA" and "BELARUS"
                    is_selected_country = (
                        geo_country_code and geo_country_code.upper() == destination_country.upper()
                    )
                    
                    # Debug tracking - ADD decision field!
                    will_skip = (not is_all_countries and not is_selected_country)
                    result['_debug_country_checks'].append({
                        'measure': measure_type[:50],
                        'geo_code': geo_country_code,
                        'geo_desc': geo_description[:50] if geo_description else 'None',
                        'is_all': is_all_countries,
                        'is_selected': is_selected_country,
                        'dest': destination_country,
                        'decision': 'FILTERED' if will_skip else 'KEPT'
                    })
                    
                    # Re-enabled: Skip if it's a different specific country (not ERGA OMNES, not selected country)
                    if not is_all_countries and not is_selected_country:
                        result['_debug_filtered_count'] += 1
                        continue
                    
                    # Add document codes from this measure (country-filtered and direction-matched)
                    if measure_id in measure_docs:
                        for doc_info in measure_docs[measure_id]['doc_codes']:
                            doc_key = doc_info['code']
                            if doc_key not in document_codes:  # Avoid duplicates
                                document_codes[doc_key] = doc_info['requirement']
                else:
                    # No country filter - add document codes from direction-matched measures
                    if measure_id in measure_docs:
                        for doc_info in measure_docs[measure_id]['doc_codes']:
                            doc_key = doc_info['code']
                            if doc_key not in document_codes:
                                document_codes[doc_key] = doc_info['requirement']
                
                measure_info = {
                    "type": measure_type,
                    "type_id": measure_type_id,
                    "duty_expression": duty_text,
                    "geographical_area": geo_description,
                    "geo_code": geo_country_code,
                    "legal_acts": attrs.get('legal_acts', []),
                }
                
                # Add conditions to measure_info if available
                if measure_id in measure_docs and measure_docs[measure_id].get('conditions'):
                    measure_info['conditions'] = measure_docs[measure_id]['conditions']
                
                # Extract duty rates
                if 'third country duty' in measure_type.lower():
                    result['third_country_duty'] = duty_text if duty_text else 'Not specified in HMRC data'
                elif 'vat' in measure_type.lower() or 'value added tax' in measure_type.lower():
                    result['vat_rate'] = duty_text if duty_text else 'Not specified in HMRC data'
                elif 'preferential' in measure_type.lower() or 'tariff preference' in measure_type.lower():
                    if geo_description:
                        result['preferential_duty'][geo_description] = duty_text if duty_text else 'Not specified'
                
                # Categorize special measure types
                measure_lower = measure_type.lower()
                
                # Suspensions (temporary 0% duty)
                if 'suspension' in measure_lower:
                    suspension_info = {
                        'type': measure_type,
                        'duty': duty_text,
                        'geographical_area': geo_description,
                        'conditions': measure_info.get('conditions', [])
                    }
                    result['suspensions'].append(suspension_info)
                
                # Quotas (Tariff Rate Quotas)
                if 'quota' in measure_lower or 'tariff rate quota' in measure_lower:
                    quota_info = {
                        'type': measure_type,
                        'duty': duty_text,
                        'geographical_area': geo_description,
                        'conditions': measure_info.get('conditions', [])
                    }
                    result['quotas'].append(quota_info)
                
                # Anti-dumping duties
                if 'anti-dumping' in measure_lower or 'dumping' in measure_lower or 'countervailing' in measure_lower:
                    ad_info = {
                        'type': measure_type,
                        'duty': duty_text,
                        'geographical_area': geo_description,
                        'conditions': measure_info.get('conditions', [])
                    }
                    result['anti_dumping'].append(ad_info)
                
                # Prohibitions and restrictions
                if 'prohibition' in measure_lower or 'restriction' in measure_lower or 'surveillance' in measure_lower:
                    prohibition_info = {
                        'type': measure_type,
                        'geographical_area': geo_description,
                        'geo_code': geo_country_code,
                        'conditions': measure_info.get('conditions', [])
                    }
                    result['prohibitions'].append(prohibition_info)
                
                # Collect unique conditions
                if measure_info.get('conditions'):
                    for cond in measure_info['conditions']:
                        if cond and cond not in result['conditions']:
                            result['conditions'].append(cond)
                
                # Get additional codes for this measure
                add_codes = rels.get('additional_codes', {}).get('data', [])
                for ac_ref in add_codes:
                    ac_id = ac_ref.get('id')
                    ac = additional_code_lookup.get(ac_id, {})
                    if ac:
                        ac_attrs = ac.get('attributes', {})
                        result['additional_codes'].append({
                            'code': ac_attrs.get('code'),
                            'description': ac_attrs.get('description'),
                            'measure_type': measure_type
                        })
                
                # Check if it's import or export measure (for categorization)
                is_import_measure = 'import' in measure_type.lower()
                is_export_measure = 'export' in measure_type.lower()
                
                if is_import_measure:
                    result["import_measures"].append(measure_info)
                if is_export_measure:
                    result["export_measures"].append(measure_info)
                
                # Add to country-specific if it matches
                if destination_country and geo_country_code:
                    result["country_specific_measures"].append(measure_info)
                
                # Add to main measures (already filtered by direction earlier)
                result["measures"].append(measure_info)
        
        # Store filtered document codes
        result['document_codes'] = document_codes

        # Build document code groups (codes grouped by their originating measure)
        # Each group contains alternative codes for the same requirement —
        # typically a restrictive code (licence/cert needed) and an exemption
        # code (licence not required).
        doc_code_groups = []
        for measure in measures:
            if measure.get('type') != 'measure':
                continue
            mid = measure.get('id')
            if mid not in measure_docs:
                continue
            # Only include measures whose codes ended up in document_codes
            group_codes = []
            for dc in measure_docs[mid]['doc_codes']:
                if dc['code'] in document_codes:
                    group_codes.append(dc)
            if not group_codes:
                continue
            # Avoid duplicate single-code groups already seen
            rels = measure.get('relationships', {})
            mt_ref = rels.get('measure_type', {}).get('data', {})
            mt_obj = measure_type_lookup.get(mt_ref.get('id', ''), {})
            mt_desc = mt_obj.get('attributes', {}).get('description', '')
            doc_code_groups.append({
                'measure': mt_desc,
                'codes': group_codes,      # [{code, requirement}, ...]
            })

        # Deduplicate groups that have identical code sets
        seen_code_sets = set()
        unique_groups = []
        for grp in doc_code_groups:
            code_set = frozenset(c['code'] for c in grp['codes'])
            if code_set not in seen_code_sets:
                seen_code_sets.add(code_set)
                unique_groups.append(grp)
        result['document_code_groups'] = unique_groups

        # Auto-select the preferred (non-restrictive) code from each group
        result['selected_document_codes'] = _pick_preferred_codes(unique_groups)
        
        # Check for supplementary units - HMRC API stores this in measurement unit fields
        # Check multiple possible locations in the API response
        supp_unit = None
        
        # Method 1: Check attributes directly
        if commodity_data.get('measurement_unit_abbreviation'):
            supp_unit = commodity_data.get('measurement_unit_abbreviation')
        elif commodity_data.get('supplementary_unit'):
            supp_unit = commodity_data.get('supplementary_unit')
        
        # Method 2: Check for measurement unit object
        if not supp_unit:
            measurement_unit = commodity_data.get('measurement_unit', {})
            if isinstance(measurement_unit, dict):
                supp_unit = measurement_unit.get('abbreviation') or measurement_unit.get('description')
        
        # Method 3: Look in included data for measurement_unit type
        if not supp_unit:
            for item in measures:
                if item.get('type') == 'measurement_unit':
                    attrs = item.get('attributes', {})
                    supp_unit = attrs.get('abbreviation') or attrs.get('description')
                    if supp_unit:
                        break
        
        result["supplementary_units"] = supp_unit if supp_unit else None
        
        # Add direction-specific information
        if direction.lower() == "import":
            result["requires_licence"] = any("licence" in m.get("type", "").lower() for m in result["import_measures"])
            result["has_quotas"] = any("quota" in m.get("type", "").lower() for m in result["import_measures"])
            
            # Determine CDS preference code for imports
            result["preference_code"] = self._determine_preference_code(result, destination_country)
        else:
            result["export_licence_required"] = any("licence" in m.get("type", "").lower() for m in result["export_measures"])
        
        return result
    
    def _determine_preference_code(self, hmrc_data: Dict, country: str = None) -> str:
        """
        Determine the 3-digit CDS preference code based on HMRC measures.
        
        CDS Preference Codes:
        - 100: Third country duty (MFN/ERGA OMNES)
        - 115: Autonomous tariff suspension (ATQ)
        - 119: Autonomous tariff suspension (ATS)
        - 120-127: Tariff rate quotas
        - 200-299: Preferential tariff agreements
        - 300: Generalised System of Preferences (GSP)
        
        Args:
            hmrc_data: Parsed HMRC commodity data
            country: Origin country code
            
        Returns:
            3-digit preference code as string
        """
        # Check for suspensions first (highest priority after specific preferences)
        suspensions = hmrc_data.get('suspensions', [])
        if suspensions:
            for susp in suspensions:
                susp_type = susp.get('type', '').lower()
                if 'autonomous' in susp_type:
                    return '115'  # Autonomous tariff suspension
                elif 'airworthiness' in susp_type or 'ships' in susp_type:
                    return '119'  # Special suspension categories
        
        # Check for quotas
        quotas = hmrc_data.get('quotas', [])
        if quotas:
            return '120'  # Tariff rate quota
        
        # Check for preferential duties
        pref_duties = hmrc_data.get('preferential_duty', {})
        if pref_duties and country:
            # Map geographic areas to preference codes
            for geo_area, duty in pref_duties.items():
                geo_lower = geo_area.lower()
                
                # Check if this preference applies to the origin country
                # This is simplified - in reality would need more complex matching
                if country.upper() in geo_area.upper():
                    # Specific trade agreement codes
                    if 'european union' in geo_lower or 'eu' in geo_lower:
                        return '200'  # EU preference
                    elif 'cariforum' in geo_lower:
                        return '211'  # CARIFORUM
                    elif 'eastern and southern africa' in geo_lower:
                        return '212'  # ESA
                    elif 'sadc' in geo_lower:
                        return '213'  # SADC EPA
                    elif 'developing countries trading scheme' in geo_lower and 'standard' in geo_lower:
                        return '300'  # DCTS Standard
                    elif 'developing countries trading scheme' in geo_lower and 'enhanced' in geo_lower:
                        return '301'  # DCTS Enhanced
                    elif 'developing countries trading scheme' in geo_lower and 'comprehensive' in geo_lower:
                        return '302'  # DCTS Comprehensive
                    else:
                        return '200'  # Generic preferential agreement
        
        # Default: Third country duty (MFN)
        return '100'
    
    def get_document_codes(self, commodity_code: str, trade_type: str = "import") -> List[Dict]:
        """
        Get required document codes for a commodity.
        
        Args:
            commodity_code: 8-10 digit commodity code
            trade_type: "import" or "export"
            
        Returns:
            List of required document codes
        """
        details = self.get_commodity_details(commodity_code)
        
        if not details or "error" in details:
            return []
        
        # Extract document codes from measures
        doc_codes = []
        measures = details.get("import_measures" if trade_type == "import" else "export_measures", [])
        
        for measure in measures:
            # Document codes would be in the measure conditions
            # This is a simplified extraction
            if "document" in measure.get("type", "").lower():
                doc_codes.append({
                    "code": measure.get("type"),
                    "description": measure.get("duty_expression", "")
                })
        
        return doc_codes
    
    def _validate_commodity_code_legacy(self, commodity_code: str) -> bool:
        """
        DEPRECATED: Legacy check that only verifies if *any* result comes back
        from get_commodity_details (including heading-level fallbacks).
        Use validate_commodity_code() instead for strict leaf-level validation.
        """
        details = self.get_commodity_details(commodity_code)
        return details is not None and "error" not in details

    def find_uk_equivalent(self, foreign_code: str, direction: str = "import") -> Optional[Dict]:
        """
        Find the UK commodity code equivalent for a potentially foreign HS code.
        
        US HTS codes share the first 6 digits (internationally harmonized) with
        UK codes, but digits 7-10 differ between US HTS and UK TARIC schedules.
        
        Strategy:
        1. Check if code is already valid in UK tariff (return as-is)
        2. Use HMRC headings API to find valid UK codes under the same 6-digit base
        3. Prefer codes whose digits 7-8 match the foreign code
        4. Fall back to broadest category under the same subheading
        
        Args:
            foreign_code: HS/HTS code (may contain spaces, dots, dashes)
            direction: "import" or "export"
            
        Returns:
            Dict with 'uk_code', 'original_code', 'description', 'converted' flag
            or None if no equivalent found
        """
        clean = foreign_code.replace(' ', '').replace('-', '').replace('.', '')
        
        if len(clean) < 6 or not clean.isdigit():
            return None
        
        # 1. Check if code already validates in UK tariff
        result = self.get_commodity_details(clean, direction=direction)
        if result and 'description' in result and 'error' not in result:
            return {
                'uk_code': result.get('commodity_code', clean),
                'original_code': foreign_code,
                'description': result.get('description', ''),
                'converted': False
            }
        
        # 2. Use headings API to find valid UK codes under the same 6-digit base
        base_6 = clean[:6]
        heading = clean[:4]
        
        # Check heading cache
        if heading not in self._heading_cache:
            try:
                url = f"{self.base_url}/uk/api/headings/{heading}"
                response = self.session.get(url, timeout=10)
                if response.status_code == 200:
                    self._heading_cache[heading] = response.json()
                else:
                    self._heading_cache[heading] = None
            except Exception:
                self._heading_cache[heading] = None
        
        heading_data = self._heading_cache.get(heading)
        if not heading_data:
            return None
        
        # Find all commodity codes starting with our 6-digit base
        candidates = []
        for item in heading_data.get('included', []):
            if item.get('type') == 'commodity':
                attrs = item.get('attributes', {})
                code = attrs.get('goods_nomenclature_item_id', '')
                if code.startswith(base_6):
                    desc = attrs.get('description', '')
                    candidates.append((code, desc))
        
        if not candidates:
            return None
        
        # 3. Prefer code whose digits 7-8 match the foreign code
        if len(clean) >= 8:
            us_sub = clean[6:8]
            for code, desc in candidates:
                if code[6:8] == us_sub:
                    return {
                        'uk_code': code,
                        'original_code': foreign_code,
                        'description': desc,
                        'converted': True
                    }
        
        # 4. Fall back to best "catch-all" code under the same 6-digit subheading
        #    Prefer "Other" categories (ending in 90xx, 99xx, 00xx) over heading-level codes
        #    Use the LAST (most specific) match rather than the broadest heading
        candidates.sort(key=lambda x: x[0])
        
        # Prefer catch-all "Other" codes (digits 7-8 are 90 or 99)
        for code, desc in reversed(candidates):
            if code[6:8] in ('90', '99', '80'):
                return {
                    'uk_code': code,
                    'original_code': foreign_code,
                    'description': desc,
                    'converted': True
                }
        
        # Otherwise use the last (most specific) code
        return {
            'uk_code': candidates[-1][0],
            'original_code': foreign_code,
            'description': candidates[-1][1],
            'converted': True
        }

    # -----------------------------------------------------------------
    # Description-based HS code lookup
    # -----------------------------------------------------------------
    # Keyword → (HS code, HMRC description hint) mapping for common boat parts.
    # Codes are 10-digit UK import TARIC codes validated against the HMRC API.
    # The mapping is ordered most-specific-first so that longer/more-specific
    # keyword phrases match before shorter generic ones.
    _BOAT_PARTS_HS_MAP = [
        # --- Very specific multi-word phrases first ---
        # Upholstery / cushion sets (year-model like "2019- XSTAR PORTBOW")
        (r'^\d{4}\s*-?\s*(?:xstar|nxt)', '9404909000', 'Upholstery/cushion sets for boats'),
        # Propellers
        (r'\bprop(?:eller)?\b', '8487109000', 'Ships\'/boats\' propellers and blades therefor'),
        # Check valves
        (r'\bcheck\s*valve\b', '8481309900', 'Check (nonreturn) valves'),
        # Thru hulls
        (r'\bthru\s*hull\b', '3917400099', 'Fittings for tubes, pipes and hoses, of plastics'),
        # Ballast bags
        (r'\bballast\s*bag\b', '3923299000', 'Sacks and bags of plastics'),
        # Ballast (tank)
        (r'\btank.*ballast\b|\bballast.*tank\b', '3925100000', 'Reservoirs, tanks, vats of plastics'),
        # Fuel module / module bolt
        (r'\bfuel\s*module\b|\bmodule.*fuel\b|\bmodule.*bolt\b', '8413302000', 'Fuel injection pumps'),
        # Shaft log
        (r'\bshaft\s*log\b', '3926909790', 'Other articles of plastics'),
        # Rub rail / rubrail
        (r'\brub\s*rail\b|\brubrail\b|\brail.*rub\b', '3916909700', 'Profile shapes of plastics'),
        # Gas spring / spring assembly (gas struts, tower springs)
        (r'\bgas\s*spring\b|\bspring.*gas\b', '8412210000', 'Hydraulic power engines and motors, linear acting (gas springs)'),
        # Gas shock
        (r'\bshock.*gas\b|\bgas.*shock\b|\bshock\b', '8412210000', 'Hydraulic power engines and motors, linear acting'),
        # Packing gland hose (specific, before generic gland)
        (r'\bpacking\s*gland\b', '4009420090', 'Tubes/pipes/hoses of vulcanised rubber'),
        # Dripless gland / packing gland
        (r'\bgland\b', '8484100000', 'Gaskets and similar joints of metal sheeting'),
        # Skin (boat upholstery covering) / sundeck skin
        (r'\bskin\b', '9404909000', 'Upholstery / cushion skin for boats'),
        (r'\bsundeck\b', '9404909000', 'Sundeck upholstery for boats'),
        # Pad / cushion / inlay (before surf/swim to avoid mismatches)
        (r'\bpad\b|\bcushion\b|\binlay\b', '9404909000', 'Mattress supports; cushion parts'),
        # Surf tab(s)
        (r'\bsurf\s*tabs?\b', '7326909890', 'Other articles of iron or steel'),
        # Swim platform bracket
        (r'\bswim\s*plat\b', '7326909890', 'Other articles of iron or steel'),
        # Module DSP / amplifier module
        (r'\bmodule.*dsp\b|\bdsp\b', '8518400090', 'Audio-frequency electric amplifiers'),
        # IPA (instrument panel assembly) / screen
        (r'\bipa\b|\binstrument\s*panel\b', '9031809800', 'Other measuring or checking instruments'),
        (r'\bscreen\b|\bmonitor\b', '8528590000', 'Other monitors'),
        # Engine flush
        (r'\bengine\s*flush\b', '8424899900', 'Mechanical appliances for projecting/dispersing liquids'),
        # Sea strainer / scoop strainer
        (r'\bstrainer\b|\bscoop\b', '8421299000', 'Filtering or purifying machinery for liquids'),
        # Hydraulic cylinders
        (r'\bcylinder\b', '8412210000', 'Hydraulic cylinders'),
        # Hinges
        (r'\bhinge\b', '8302410000', 'Base-metal mountings and fittings'),
        # Helm (steering)
        (r'\bhelm\b', '8479899790', 'Steering mechanisms'),
        # Remote control
        (r'\bremote\b', '8526919000', 'Radio remote controls'),

        # --- Product-type keywords ---
        # Tanks (general plastic)
        (r'\btank\b', '3925100000', 'Reservoirs, tanks, vats of plastics'),
        # Anodes (aluminum)
        (r'\banode\b', '7616999099', 'Other articles of aluminium'),
        # Gaskets / seals / O-rings (before shaft for "SEAL-DRIPLESS SHAFT")
        (r'\bgasket\b|\bseal\b|\bo-ring\b', '4016930000', 'Gaskets, washers of vulcanised rubber'),
        # Shafts
        (r'\bshaft\b', '8483109200', 'Transmission shafts'),
        # Pumps / impellers
        (r'\bimpeller\b', '8413919000', 'Parts of pumps'),
        (r'\bpump\b', '8413709100', 'Centrifugal pumps'),
        # Hoses (rubber and plastic)
        (r'\bhose.*exhaust\b|\bexhaust.*hose\b', '4009420090', 'Tubes/pipes/hoses of vulcanised rubber'),
        (r'\bhose\b', '4009420090', 'Tubes/pipes/hoses of vulcanised rubber'),
        # Valves
        (r'\bvalve\b', '8481809900', 'Other taps, cocks, valves'),
        # Fittings (plastic default for boat plumbing)
        (r'\bfitting\b', '3917400099', 'Fittings for tubes/pipes of plastics'),
        # Cables - control (mechanical)
        (r'\bcable.*control\b|\bcontrol.*cable\b', '7312109800', 'Stranded wire of iron/steel'),
        (r'\bcable.*steer\b|\bsteer.*cable\b', '7312109800', 'Stranded wire of iron/steel'),
        # Cables - electrical & harnesses
        (r'\bharness\b', '8544429090', 'Other electric conductors'),
        (r'\bcable\b', '8544429090', 'Other electric conductors'),
        # Bearings / bushings
        (r'\bbearing\b|\bbushing\b', '8483300000', 'Bearing housings; plain shaft bearings'),
        # Switches
        (r'\bswitch\b', '8536509000', 'Other switches'),
        # Speakers
        (r'\bspeaker\b|\bsubwoofer\b|\bcoaxial\b|\bgrille?\s*speaker\b', '8518290090', 'Loudspeakers'),
        # Amplifiers
        (r'(?<!\d[-\s])\bamp\b|\bamplifier\b', '8518400090', 'Audio-frequency electric amplifiers'),
        # Lights
        (r'\blight.*nav\b|\bnav.*light\b', '8512200000', 'Other lighting/visual signalling equipment'),
        (r'\blight.*dock\b|\bdock.*light\b', '8512200000', 'Other lighting/visual signalling equipment'),
        (r'\blight.*under\b|\bunderwater.*light\b', '8512200000', 'Other lighting/visual signalling equipment'),
        (r'\blight.*tower\b|\btower.*light\b', '8512200000', 'Other lighting/visual signalling equipment'),
        (r'\blights?\b|\blamp\b|\bled\b|\bunderwater', '8512200000', 'Other lighting/visual signalling equipment'),
        # Sensors
        (r'\bsensor.*temp\b|\btemp.*sensor\b', '9025808000', 'Other thermometers/instruments'),
        (r'\bsensor\b', '9025808000', 'Other thermometers/instruments'),
        # Senders (fuel/water gauges)
        (r'\bsender.*fuel\b|\bfuel.*sender\b', '9026809900', 'Other measuring instruments for liquids'),
        (r'\bsender.*water\b|\bwater.*sender\b', '9026809900', 'Other measuring instruments for liquids'),
        (r'\bsender\b', '9026809900', 'Other measuring instruments for liquids'),
        # Struts
        (r'\bstrut\b', '7326909890', 'Other articles of iron or steel'),
        # Keys
        (r'\bkey\b', '7318240000', 'Cotters and cotter-pins'),
        # Brackets
        (r'\bbracket\b', '7326909890', 'Other articles of iron or steel'),
        # Heaters
        (r'\bheater.*core\b|\bcore.*heater\b', '7322190000', 'Other radiators and parts thereof'),
        (r'\bheater.*blower\b|\bblower.*motor\b', '8414599500', 'Other fans'),
        (r'\bheater\b', '7322190000', 'Other radiators and parts thereof'),
        # Cameras
        (r'\bcamera\b', '8525801900', 'Television cameras'),
        # Horns
        (r'\bhorn\b', '8512300000', 'Sound signalling equipment'),
        # Relays
        (r'\brelay\b', '8536490000', 'Relays'),
        # Circuit breakers
        (r'\bbreaker\b', '8536200000', 'Automatic circuit breakers'),
        # Rudders / fins (ship parts)
        (r'\brudder\b', '8487900000', 'Ship machinery parts'),
        (r'\bfins?\b', '7616999099', 'Other articles of aluminium'),
        # Tires
        (r'\btire\b|\btyre\b', '4011909000', 'Other new pneumatic tyres of rubber'),
        # Mufflers (marine engine parts)
        (r'\bmuffler\b', '8409919000', 'Parts for spark-ignition engines'),
        # Drains
        (r'\bdrain\b', '3917400099', 'Fittings for tubes/pipes of plastics'),
        # Pedestals / seats
        (r'\bpedestal\b|\bseat\b', '9401990000', 'Parts of seats'),
        # Mirrors
        (r'\bmirror\b', '7009100000', 'Rear-view mirrors for vehicles'),
        # Stereo / Radio
        (r'\bstereo\b|\bradio\b', '8527210000', 'Radio receivers, combined with sound recording'),
        # Thrusters
        (r'\bthruster\b', '8487900000', 'Ship machinery parts'),
        # Decals
        (r'\bdecal\b', '4908100000', 'Transfers (decalcomanias)'),
        # Fenders
        (r'\bfender\b', '4016999790', 'Other articles of vulcanised rubber'),
        # Latches / locks
        (r'\blatch\b', '8302410000', 'Base-metal mountings and fittings'),
        # Chargers
        (r'\bcharger\b', '8504409900', 'Static converters'),
        # Screws
        (r'\bscrew\b', '7318159500', 'Other screws and bolts'),
        # Nuts
        (r'\bnut\b(?!.*cocoa)(?!.*coir)', '7318160000', 'Nuts of iron or steel'),
        # Cleats
        (r'\bcleat\b', '7326909890', 'Other articles of iron or steel'),
        # Actuators
        (r'\bactuator\b', '8412310000', 'Linear acting hydraulic/pneumatic engines'),
        # Solenoids
        (r'\bsolenoid\b', '8505200000', 'Electromagnetic couplings, clutches and brakes'),
        # Fuses
        (r'\bfuse\b', '8536100000', 'Fuses'),
        # Pins
        (r'\bpin\b', '7318240000', 'Cotters and cotter-pins'),
        # Clips (plastic)
        (r'\bclip\b', '3926909790', 'Other articles of plastics'),
        # Rivets (plastic)
        (r'\brivet\b.*plastic\b|\bplastic\b.*rivet\b', '3926909790', 'Other articles of plastics'),
        (r'\brivet\b', '7318159500', 'Rivets'),
        # Eyes / hooks
        (r'\beye\b|\bhook\b', '7326909890', 'Other articles of iron or steel'),
        # Plates / covers (metal deck)
        (r'\bplate.*deck\b|\bdeck.*plate\b', '7326909890', 'Other articles of iron or steel'),
        (r'\bcover\b', '7326909890', 'Other articles of iron or steel'),
        # Cup holders (plastic)
        (r'\bcup\s*holder\b', '3926909790', 'Other articles of plastics'),
        # Spacers (plastic)
        (r'\bspacer\b', '3926909790', 'Other articles of plastics'),
        # Bimini / sunshade
        (r'\bbimini\b|\bsunshade|\bsun\s*shade\b', '6306120000', 'Tarpaulins, awnings of synthetic fibres'),
        # Rails / inserts (SS)
        (r'\binsert.*rail\b|\brail.*insert\b|\binsert.*s/s\b|\binsert.*ss\b', '7326909890', 'Other articles of iron or steel'),
        # Tower (wakeboard tower, boat tower)
        (r'\btower\b', '7616999099', 'Other articles of aluminium (boat tower)'),
        # Springs (general)
        (r'\bspring\b', '7320209000', 'Springs of iron or steel'),
        # Pylon
        (r'\bpylon\b', '7616999099', 'Other articles of aluminium'),
        # Socket / receptacle (electrical)
        (r'\bsocket\b|\brecept\b', '8536909500', 'Other electrical apparatus for connections'),
        # Throttle / control lever
        (r'\bthrottle\b|\bcontrol.*lever\b', '8479899790', 'Other machines and mechanical appliances'),
        # Pickup (water intake)
        (r'\bpickup\b', '3917400099', 'Fittings for tubes/pipes of plastics'),
        # Vent
        (r'\bvent\b', '3917400099', 'Fittings for tubes/pipes of plastics'),
        # Ring (speaker)
        (r'\bring\b', '7326909890', 'Other articles of iron or steel'),
        # Lever
        (r'\blever\b', '7326909890', 'Other articles of iron or steel'),
        # Fill (fuel fill cap)
        (r'\bfill\b.*fuel\b|\bfuel\b.*fill\b', '7326909890', 'Other articles of iron or steel'),
        # Gauge
        (r'\bgauge\b', '9026201700', 'Instruments for measuring pressure'),
        # Line (fuel line)
        (r'\bline.*fuel\b|\bfuel.*line\b', '4009420090', 'Tubes/pipes/hoses of vulcanised rubber'),
        # Telematics / electronics module
        (r'\btelematics\b', '8526919000', 'Other radio navigational aid apparatus'),
        # Biducer / transducer
        (r'\bbiducer\b|\btransducer\b', '9015800000', 'Other surveying instruments'),
        # Controller
        (r'\bcontroller\b', '8537109100', 'Programmable controllers'),
        # Jack (trailer)
        (r'\bjack\b', '8425490000', 'Other jacks and hoists'),
        # Shim
        (r'\bshim\b', '7326909890', 'Other articles of iron or steel'),
        # Caliper
        (r'\bcaliper\b', '8708309100', 'Brake parts'),
        # Axle
        (r'\baxle\b', '8708999700', 'Other parts and accessories of motor vehicles'),
        # Bag (general)
        (r'\bbag\b', '3923299000', 'Sacks and bags of plastics'),
        # Flange
        (r'\bflange\b', '7307210000', 'Flanges of stainless steel'),
    ]

    import re as _re

    def lookup_hs_from_description(self, description: str) -> dict:
        """
        Determine an HS commodity code from a product description using
        keyword matching.  Designed for boat parts / marine accessories.

        Args:
            description: The product description text (e.g. "PUMP-BALLAST 13.7 GPM")

        Returns:
            dict with keys:
                commodity_code: 10-digit HS code or "" if no match
                hmrc_description: Tariff description hint
                match_keyword: The regex pattern that matched
                confidence: float 0-1 indicating match quality
        """
        if not description:
            return {"commodity_code": "", "hmrc_description": "", "match_keyword": "", "confidence": 0.0}

        desc_lower = description.lower()

        for pattern, code, hint in self._BOAT_PARTS_HS_MAP:
            if self._re.search(pattern, desc_lower):
                return {
                    "commodity_code": code,
                    "hmrc_description": hint,
                    "match_keyword": pattern,
                    "confidence": 0.65,
                }

        return {"commodity_code": "", "hmrc_description": "", "match_keyword": "", "confidence": 0.0}
