"""
HMRC Trade Tariff API integration module
API Documentation: https://www.trade-tariff.service.gov.uk/api/v2/
"""
import requests
from typing import Dict, Optional, List
import time


class HMRCTariffAPI:
    """
    Interface to HMRC Trade Tariff API for commodity code lookups
    """
    
    def __init__(self, base_url: str = "https://www.trade-tariff.service.gov.uk"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'CustomsInvoiceTool/1.0'
        })
    
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
                    return self._parse_commodity_response(data, direction, destination_country, export_only)
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
        return last_error or {"error": f"Code {commodity_code} not found"}
    
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
        
        result["supplementary_units"] = supp_unit
        
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
    
    def validate_commodity_code(self, commodity_code: str) -> bool:
        """
        Check if a commodity code is valid.
        
        Args:
            commodity_code: Code to validate
            
        Returns:
            True if valid, False otherwise
        """
        details = self.get_commodity_details(commodity_code)
        return details is not None and "error" not in details
