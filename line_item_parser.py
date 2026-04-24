"""
Line Item Parser - Pass 2: Extract structured data from extracted text
Handles multi-page items and uses proven parsing from pdf_extractor.py
Uses GPT-4o when an OpenAI API key is available (st.secrets["OPENAI_API_KEY"]
or the OPENAI_API_KEY environment variable); falls back to regex otherwise.
"""
import os
import re
from typing import List, Dict, Tuple, Optional
import json
from pathlib import Path


class LineItemParser:
    """Parse line items from extracted page text using proven patterns"""
    
    def parse_job_items(self, job_dir: Path, direction: str = "export") -> Dict:
        """
        Parse line items from a completed job's pages.json
        
        Args:
            job_dir: Path to job directory
            direction: "export" or "import"
            
        Returns:
            Dict with parsed items and metadata
        """
        pages_json_path = job_dir / "pages.json"
        
        if not pages_json_path.exists():
            return {"error": "pages.json not found", "items": []}
        
        with open(pages_json_path) as f:
            pages_data = json.load(f)
        
        # Concatenate all successful page texts
        all_text = ""
        page_map = {}  # Track which page each character position is on
        
        for page in pages_data.get("pages", []):
            if page.get("status") == "success":
                page_num = page.get("page_number")
                text = page.get("text", "")
                start_pos = len(all_text)   
                all_text += f"\n--- PAGE {page_num} ---\n{text}\n"
                end_pos = len(all_text)
                
                # Map every position to this page
                for pos in range(start_pos, end_pos):
                    page_map[pos] = page_num
        
        if not all_text:
            return {"error": "No text extracted", "items": []}

        # ── LLM path ──────────────────────────────────────────────────────────
        # Priority: 1) Google Gemini (free tier) → 2) OpenAI → 3) regex fallback

        # 1. Google Gemini Flash — free tier, 1,500 req/day, no credit card needed
        google_key = self._get_secret("GOOGLE_API_KEY")
        if google_key:
            try:
                from llm_extractor import extract_with_gemini
                llm_items, llm_meta = extract_with_gemini(all_text, google_key)
                if llm_items:
                    return {
                        "total_items": len(llm_items),
                        "items": llm_items,
                        "metadata": llm_meta,
                        "pages_analyzed": len(pages_data.get("pages", [])),
                        "direction": direction,
                        "format_type": "llm_gemini",
                    }
            except Exception as _err:
                print(f"[Gemini extractor] failed, trying OpenAI: {_err}")

        # 2. OpenAI GPT-4o-mini — ~£0.001 per invoice
        openai_key = self._get_secret("OPENAI_API_KEY")
        if openai_key:
            try:
                from llm_extractor import extract_with_llm
                llm_items, llm_meta = extract_with_llm(all_text, openai_key)
                if llm_items:
                    return {
                        "total_items": len(llm_items),
                        "items": llm_items,
                        "metadata": llm_meta,
                        "pages_analyzed": len(pages_data.get("pages", [])),
                        "direction": direction,
                        "format_type": "llm_gpt4o_mini",
                    }
            except Exception as _err:
                print(f"[OpenAI extractor] failed, falling back to regex: {_err}")

        # ── Regex fallback ────────────────────────────────────────────────────
        # Parse items using the proven logic from pdf_extractor.py
        items, format_type = self._parse_line_items_proven(all_text, direction, page_map)

        # Post-process extracted items to remove invoice-level noise and
        # apply lightweight deduplication so different formats don't produce
        # spurious extra rows.
        items = self._postprocess_items(items)

        return {
            "total_items": len(items),
            "items": items,
            "pages_analyzed": len(pages_data.get("pages", [])),
            "direction": direction,
            "format_type": format_type
        }

    def _get_secret(self, key_name: str) -> Optional[str]:
        """
        Return a secret value from (in priority order):
          1. Streamlit secrets  (st.secrets[key_name])
          2. Environment variable
        Returns None if not configured.
        """
        # Try Streamlit secrets first (works on Streamlit Cloud)
        try:
            import streamlit as st
            key = st.secrets.get(key_name, "")
            if key:
                return key
        except Exception:
            pass
        # Fall back to environment variable (local dev)
        return os.getenv(key_name) or None

    def _postprocess_items(self, items: List[Dict]) -> List[Dict]:
        """Filter out obvious invoice-level rows (totals/terms/etc.) and dedupe.

        Strategy:
          - Remove items whose description looks like a footer/summary/terms
            (simple token-based blacklist).
          - Remove items that clearly look non-product (delegates to
            `_is_valid_item`).
          - Deduplicate by a stable key: `(commodity_code, quantity, total_value)`
            falling back to normalized description when HS missing.
        """
        if not items:
            return items

        footer_tokens = [
            'total product', 'shipping cost', 'in total', 'terms & conditions',
            'signed by', 'date:', 'currency:', 'method of payment', 'payment term',
            'total invoice', 'invoice total', 'grand total', 'shipping', 'total'
        ]

        kept: List[Dict] = []
        seen_keys = set()

        for it in items:
            desc = (it.get('description') or '').strip()
            desc_l = desc.lower()

            # Drop obvious footer/summary lines
            if any(tok in desc_l for tok in footer_tokens):
                continue

            # Drop short/garbage descriptions
            if not desc or len(re.sub(r'\s+', '', desc)) < 3:
                continue

            # Use existing validator as a conservative guard
            if not self._is_valid_item(desc, it.get('stock_number', ''), it.get('quantity', ''), it.get('total_value', '')):
                continue

            # Build dedupe key — include stock number when available so that
            # two different products with the same HS/qty/value are not merged.
            key_parts = []
            cc = (it.get('commodity_code') or '').strip()
            qty = str(it.get('quantity') or '').strip()
            tv = str(it.get('total_value') or '').strip()
            sn = (it.get('stock_number') or '').strip()
            if sn:
                key_parts = [sn, qty, tv]
            elif cc:
                key_parts = [cc, qty, tv]
            else:
                # fallback to normalized description prefix
                key_parts = [desc_l[:40], qty, tv]

            key = tuple(key_parts)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Normalise UK -> GB (HMRC does not recognise "UK" as a country code)
            for field in ('country_of_origin', 'country_origin'):
                if it.get(field, '').strip().upper() == 'UK':
                    it[field] = 'GB'

            kept.append(it)

        return kept
    
    def _parse_line_items_proven(self, text: str, direction: str, page_map: Dict) -> List[Dict]:
        """Use the proven parsing logic with enhanced table detection"""
        items = []
        lines = text.split('\n')
        self._last_table_format = None
        
        # First, try to detect if this is a tabular invoice format
        # Look for "HS Codes" column header (might be on its own line in vertical format)
        has_table_format = False
        table_start_idx = -1
        
        for i, line in enumerate(lines):
            # Look for HS/Commodity Code header variants - check broader patterns
            if any(phrase in line for phrase in ['HS Codes', 'HS Code', 'Commodity Code', 'Commodity', '| Code |', 'Product Description']):
                # Check if nearby lines (within 10 lines before) have other table headers
                context_start = max(0, i - 10)
                context_lines = ' '.join(lines[context_start:i+1]).lower()
                if any(keyword in context_lines for keyword in ['item', 'stock', 'product', 'description', 'unit', 'quantity', 'amount', 'line', 'qty', 'pack']):
                    has_table_format = True
                    table_start_idx = i
                    break

        # If we detected table format, use table parsing
        if has_table_format:
            items = self._parse_tabular_format(lines, table_start_idx, direction, page_map)
            # Determine sub-format (vertical vs horizontal) for reporting
            fmt = self._last_table_format or "table"
            return items, fmt
        
        # Otherwise, use the original pattern-based parsing
        return self._parse_pattern_format(lines, direction, page_map), "pattern"
    
    def _parse_tabular_format(self, lines: List[str], table_start: int, direction: str, page_map: Dict) -> List[Dict]:
        """Parse invoices with tabular format - handles both vertical and horizontal layouts"""
        items = []
        pad_to_10 = (direction.lower() == "import")
        
        # Detect if table is vertical (fields on separate lines) or horizontal (all on one row)
        # Check a few lines after header
        is_vertical = False
        for i in range(table_start + 1, min(table_start + 20, len(lines))):
            line = lines[i].strip()
            # Vertical format: line with just a digit "1", followed by lines with just text
            if line.isdigit() and int(line) == 1:
                # Check if next lines are individual fields (not all data on one row)
                if i + 3 < len(lines):
                    # In vertical, next lines should have short individual values
                    # In horizontal, next line would have all data with multiple numbers and HS code at end
                    next_line = lines[i + 1].strip()
                    # If next line doesn't end with 8-digit HS code, likely vertical
                    if not re.search(r'\d{8}\s*$', next_line):
                        is_vertical = True
                break
        
        if is_vertical:
            self._last_table_format = "vertical_table"
            return self._parse_vertical_table(lines, table_start, direction, page_map)
        else:
            self._last_table_format = "horizontal_table"
            return self._parse_horizontal_table(lines, table_start, direction, page_map)
    
    def _parse_vertical_table(self, lines: List[str], table_start: int, direction: str, page_map: Dict) -> List[Dict]:
        """Parse vertical table format where each field is on a separate line"""
        items = []
        pad_to_10 = (direction.lower() == "import")
        seen_descriptions = set()  # Track descriptions to avoid duplicates
        
        # Find where data starts (first line with just a digit after headers)
        data_start = table_start + 1
        for i in range(table_start + 1, len(lines)):
            if lines[i].strip().isdigit() and int(lines[i].strip()) == 1:
                data_start = i
                break
        
        # Detect the stride by finding distance between item numbers
        # Look for pattern: "1" ... "2" to determine stride
        stride = 10  # Default
        for i in range(data_start + 1, min(data_start + 20, len(lines))):
            if lines[i].strip() == '2':
                stride = i - data_start
                break
        # debug removed
        
        # Detect sub-format: IKF-style (stride 12, has description+UOM+CofO inline)
        # vs ATI-style (stride 10, country ISO code at position 2, descriptions in separate section)
        # vs RS-style (stride 11, has inline description after stock, country after description)
        is_ikf_format = False
        is_rs_format = False
        
        if stride >= 11:
            # Check if there's a "CofO" line within the first item
            for j in range(data_start + 1, min(data_start + stride + 2, len(lines))):
                if lines[j].strip() == 'CofO':
                    is_ikf_format = True
                    break
            
            # If not IKF, check for RS format: item, stock, DESCRIPTION (not country), country, hs-code
            if not is_ikf_format and stride == 11:
                # In RS format: i+2 should be a description (not a 2-letter country code)
                # Check the third line after item number (i+2)
                if data_start + 2 < len(lines):
                    field_i2 = lines[data_start + 2].strip()
                    # If it's not a 2-letter country code, it's probably a description
                    if not re.match(r'^[A-Z]{2}$', field_i2) and len(field_i2) > 3:
                        is_rs_format = True
        
        if is_ikf_format:
            self._last_table_format = "vertical_table_ikf"
            return self._parse_vertical_table_ikf(lines, data_start, stride, direction, page_map, pad_to_10)
        
        if is_rs_format:
            self._last_table_format = "vertical_table_rs"
            return self._parse_vertical_table_rs(lines, data_start, stride, direction, page_map, pad_to_10)
        
        # ATI format: item#, stock, country, hs_code, coc, qty, unit_price, amount, unit_wt, line_wt (10 lines)
        i = data_start
        current_page = 1  # Track which page we're on
        while i < len(lines):
            line = lines[i].strip()

            # Track page separators injected by parse_job_items
            m_page = re.match(r'^---\s*PAGE\s*(\d+)\s*---', line)
            if m_page:
                current_page = int(m_page.group(1))
                i += 1
                continue

            # Check if this is an item number (starts a new item)
            if not line.isdigit():
                i += 1
                continue
            
            item_num = line
            
            # Need at least stride-1 more lines for item data
            if i + stride - 1 >= len(lines):
                break
            
            # Extract fields based on ATI format:
            # i+0: item number, i+1: stock, i+2: country, i+3: hs_code, i+4: coc
            # i+5: quantity, i+6: unit_price, i+7: amount, i+8: unit_weight, i+9: line_weight
            
            # Handle stock numbers that wrap to multiple lines
            # Valid country codes are 2-letter ISO codes (CN, UK, DE, US, etc.)
            valid_countries = {'CN', 'UK', 'GB', 'DE', 'US', 'JP', 'KR', 'TW', 'IT', 'FR', 'ES', 
                              'NL', 'BE', 'PL', 'SE', 'CZ', 'AT', 'DK', 'HU', 'IN', 'MX', 'CA',
                              'AU', 'SG', 'HK', 'MY', 'TH', 'VN', 'CH', 'IE', 'PT', 'NO', 'FI'}
            
            # Scan from i+1 to find the country code (stock may wrap to multiple lines)
            stock_parts = []
            offset = 0
            country_pos = -1
            for j in range(i + 1, min(i + 6, len(lines))):  # Stock can be up to 4 lines
                field = lines[j].strip().upper()
                if field in valid_countries:
                    country_pos = j
                    break
                stock_parts.append(lines[j].strip())
            
            if country_pos == -1:
                # No valid country found — try alternate simple vertical layout
                # where fields follow as: desc (possibly multi-line), hs_code, unit_price, qty, unit, total
                alt_ok = False
                desc_alt = ''
                hs_code_alt = ''
                unit_value_alt = ''
                quantity_alt = ''
                total_value_alt = ''

                # Search ahead for an HS-code-like line and treat preceding lines as description
                for j in range(i + 1, min(i + 8, len(lines))):
                    if re.match(r'^\d{6,10}$', lines[j].strip()):
                        hs_code_alt = lines[j].strip()
                        desc_alt = ' '.join(ln.strip() for ln in lines[i+1:j]).strip()
                        # unit price likely at j+1, qty at j+2, total maybe at j+4
                        unit_value_alt = self._parse_monetary_value(lines[j+1].strip()) if j+1 < len(lines) else ''
                        qty_raw_alt = lines[j+2].strip() if j+2 < len(lines) else ''
                        try:
                            quantity_alt = str(int(float(qty_raw_alt.replace(',', '')))) if qty_raw_alt else ''
                        except Exception:
                            quantity_alt = qty_raw_alt
                        total_value_alt = self._parse_monetary_value(lines[j+4].strip()) if j+4 < len(lines) else ''

                        # Basic validation of HS and quantity
                        if re.match(r'^\d{6,10}$', hs_code_alt):
                            try:
                                qv = float(quantity_alt.replace(',', '.')) if quantity_alt else 0
                                if 0 < qv <= 100000:
                                    alt_ok = True
                            except Exception:
                                alt_ok = False
                        break

                if alt_ok:
                    hs_code = hs_code_alt
                    stock_no = ''
                    country = ''
                    coc = ''
                    quantity = quantity_alt
                    unit_value = unit_value_alt
                    total_value = total_value_alt
                    description = desc_alt
                    coo = ''

                    # Basic sanity checks: non-empty description and valid item
                    if not description or len(description.strip()) < 4:
                        i += stride
                        continue

                    # Filter out obvious footer/summary lines used as items
                    footer_tokens = ['total product', 'shipping cost', 'in total', 'terms & conditions', 'signed by', 'date:']
                    if any(tok in description.lower() for tok in footer_tokens):
                        i += stride
                        continue

                    # Pad HS code
                    hs_code = self._pad_hs_code(hs_code, pad_to_10)

                    confidence = 0.5
                    if quantity and quantity != '1':
                        confidence += 0.1
                    if total_value:
                        confidence += 0.1

                    # Validate item using existing comprehensive checks
                    if not self._is_valid_item(description, stock_no, quantity, total_value):
                        i += stride
                        continue

                    items.append({
                        "stock_number": stock_no,
                        "description": description,
                        "quantity": quantity,
                        "uom": "EA",
                        "unit_value": unit_value,
                        "total_value": total_value,
                        "currency": "GBP",
                        "commodity_code": hs_code,
                        "country_of_origin": coo,
                        "unit_weight": "",
                        "net_weight": "",
                        "pages": [current_page],
                        "_page": current_page,
                        "confidence": round(confidence, 2),
                        "needs_review": confidence < 0.7,
                        "raw_text": f"{item_num} {description} {hs_code}"
                    })
                    i += stride
                    continue
                else:
                    # Alternate layout not detected; skip this candidate
                    i += stride
                    continue
            
            offset = country_pos - (i + 2)  # How many extra lines for stock
            # If country is at position i+1, there's no stock number
            if country_pos == i + 1:
                stock_no = ""
            else:
                stock_no = ' '.join(stock_parts) if stock_parts else ""
            
            country = lines[country_pos].strip().upper()
            hs_code = lines[country_pos + 1].strip()
            coc = lines[country_pos + 2].strip()  # Certificate of Conformity
            
            # Parse quantity - always integer, commas are thousands separators (1,000 = 1000)
            quantity_raw = lines[country_pos + 3].strip().replace(',', '')
            try:
                quantity = str(int(float(quantity_raw)))  # Convert to int, handles "1.0" -> "1"
            except ValueError:
                quantity = quantity_raw
            
            # Parse monetary values - handle both European (comma=decimal) and UK (comma=thousands) formats
            unit_value = self._parse_monetary_value(lines[country_pos + 4].strip())
            total_value = self._parse_monetary_value(lines[country_pos + 5].strip())
            
            # Weights use dot as decimal
            unit_weight = lines[country_pos + 6].strip().replace(',', '.')
            net_weight = lines[country_pos + 7].strip().replace(',', '.')
            
            # Use stock number as description (descriptions are in separate section)
            description = stock_no
            
            # Country of origin from the country field
            coo = self._country_to_iso(country)
            
            # Check for footer markers
            item_text = (stock_no + ' ' + hs_code).lower()
            if any(marker in item_text for marker in ['bank details:', 'please remit to:', 'total invoice value:', 'grand total:', 'payment terms', 'account number', 'product information']):
                break
            
            # VALIDATION 1: HS code must be 6-10 digits
            if not re.match(r'^\d{6,10}$', hs_code):
                i += stride + offset
                continue
            
            # VALIDATION 2: Quantity should be numeric and reasonable
            try:
                qty_val = float(quantity.replace(',', '.'))
                if qty_val <= 0 or qty_val > 100000:
                    i += stride + offset
                    continue
            except ValueError:
                i += stride + offset
                continue
            
            # Pad HS code to proper length
            hs_code = self._pad_hs_code(hs_code, pad_to_10)
            
            # Calculate confidence
            confidence = 0.6  # Base for structured format
            if quantity and quantity != "1":
                confidence += 0.1
            if total_value:
                confidence += 0.1
            if coo:
                confidence += 0.1
            if net_weight:
                confidence += 0.1
            
            items.append({
                "stock_number": stock_no,
                "description": description,
                "quantity": quantity,
                "uom": "EA",  # Default, could parse from COC field
                "unit_value": unit_value,
                "total_value": total_value,
                "currency": "GBP",
                "commodity_code": hs_code,
                "country_of_origin": coo,
                "unit_weight": unit_weight,
                "net_weight": net_weight,
                "pages": [current_page],
                "_page": current_page,
                "confidence": round(confidence, 2),
                "needs_review": confidence < 0.7,
                "raw_text": f"{item_num} {stock_no}"
            })
            
            # Move to next item (add offset if stock wrapped to extra line)
            i += stride + offset
        
        # Match descriptions from PRODUCT INFORMATION LIST sections
        items = self._match_product_info_descriptions(lines, items)
        
        return items
    
    def _parse_vertical_table_ikf(self, lines: List[str], data_start: int, stride: int, 
                                   direction: str, page_map: Dict, pad_to_10: bool) -> List[Dict]:
        """Parse IKF/RS Components vertical format.
        
        Layout per item (stride typically 12):
          +0: item number
          +1: stock number
          +2: description
          +3: UOM (EA, BAG, etc.)
          +4: quantity
          +5: unit price
          +6: amount (total value)
          +7: unit weight
          +8: line weight (net weight)
          +9: HS code (8 digits)
          +10: "CofO"
          +11: country name (full, e.g. "China", "United Kingdom")
        """
        items = []
        i = data_start
        current_page = 1
        
        # Country name -> ISO mapping for common names
        country_name_map = {
            'china': 'CN', 'united kingdom': 'GB', 'germany': 'DE', 'italy': 'IT',
            'czech republic': 'CZ', 'taiwan': 'TW', 'romania': 'RO', 'mexico': 'MX',
            'switzerland': 'CH', 'france': 'FR', 'spain': 'ES', 'japan': 'JP',
            'south korea': 'KR', 'india': 'IN', 'usa': 'US', 'united states': 'US',
            'netherlands': 'NL', 'belgium': 'BE', 'poland': 'PL', 'sweden': 'SE',
            'austria': 'AT', 'denmark': 'DK', 'hungary': 'HU', 'canada': 'CA',
            'australia': 'AU', 'singapore': 'SG', 'hong kong': 'HK', 'malaysia': 'MY',
            'thailand': 'TH', 'vietnam': 'VN', 'ireland': 'IE', 'portugal': 'PT',
            'norway': 'NO', 'finland': 'FI', 'turkey': 'TR', 'brazil': 'BR',
            'korea, republic of': 'KR', 'great britain': 'GB',
        }
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Track page separators
            m_page = re.match(r'^---\s*PAGE\s*(\d+)\s*---', line)
            if m_page:
                current_page = int(m_page.group(1))
                i += 1
                continue
            
            # Skip non-item-number lines
            if not line.isdigit():
                i += 1
                continue
            
            item_num = line
            
            # Need enough lines ahead for this item
            if i + 9 >= len(lines):
                break
            
            # Read fields relative to item number position
            stock_no = lines[i + 1].strip()
            description = lines[i + 2].strip()
            uom = lines[i + 3].strip()
            
            # Quantity — handle European comma-as-thousands and plain integers
            quantity_raw = lines[i + 4].strip().replace(',', '')
            try:
                quantity = str(int(float(quantity_raw)))
            except ValueError:
                quantity = quantity_raw
            
            unit_value = self._parse_monetary_value(lines[i + 5].strip())
            total_value = self._parse_monetary_value(lines[i + 6].strip())
            unit_weight = lines[i + 7].strip().replace(',', '.')
            net_weight = lines[i + 8].strip().replace(',', '.')
            
            # HS code — scan from i+9 onward to find the 8-digit code
            # (in case amounts with spaces pushed lines)
            hs_code = ''
            cofo_offset = 0
            for j in range(i + 9, min(i + stride + 2, len(lines))):
                candidate = lines[j].strip()
                if re.match(r'^\d{6,10}$', candidate):
                    hs_code = candidate
                    cofo_offset = j - i
                    break
            
            if not hs_code:
                # No valid HS code found — skip this item
                i += stride
                continue
            
            # Country of origin: expect "CofO" then country name after HS code
            country_iso = ''
            if cofo_offset + 2 < stride + 3:
                cofo_line = lines[i + cofo_offset + 1].strip() if i + cofo_offset + 1 < len(lines) else ''
                country_line = lines[i + cofo_offset + 2].strip() if i + cofo_offset + 2 < len(lines) else ''
                if cofo_line == 'CofO' and country_line:
                    country_iso = country_name_map.get(country_line.lower(), country_line[:2].upper())
            
            # Check for footer markers
            combined_text = (stock_no + ' ' + description + ' ' + hs_code).lower()
            if any(m in combined_text for m in ['bank details:', 'please remit', 'total invoice', 
                                                 'grand total', 'payment terms', 'account number']):
                break
            
            # Validate
            try:
                qty_val = float(quantity.replace(',', '.'))
                if qty_val <= 0 or qty_val > 100000:
                    i += stride
                    continue
            except ValueError:
                i += stride
                continue
            
            hs_code = self._pad_hs_code(hs_code, pad_to_10)
            
            confidence = 0.7  # Higher base — has inline description
            if total_value:
                confidence += 0.1
            if country_iso:
                confidence += 0.1
            if net_weight:
                confidence += 0.1
            
            items.append({
                "item_number": item_num,
                "stock_number": stock_no,
                "description": description,
                "quantity": quantity,
                "uom": uom if uom else "EA",
                "unit_value": unit_value,
                "total_value": total_value,
                "currency": "GBP",
                "commodity_code": hs_code,
                "country_of_origin": country_iso,
                "unit_weight": unit_weight,
                "net_weight": net_weight,
                "pages": [current_page],
                "_page": current_page,
                "confidence": round(min(confidence, 1.0), 2),
                "needs_review": False,
                "raw_text": f"{item_num} {stock_no} {description}"
            })
            
            i += stride
        
        return items

    def _parse_vertical_table_rs(self, lines: List[str], data_start: int, stride: int,
                                   direction: str, page_map: Dict, pad_to_10: bool) -> List[Dict]:
        """Parse RS Components vertical format with inline descriptions.
        
        Layout per item (stride typically 11):
          +0: item number
          +1: stock number
          +2: description (inline, not in separate section)
          +3: country_of_origin (2-letter code)
          +4: HS code (8 digits)
          +5: UOM/quantity combo (e.g. "1 OF 1", "1 BAG OF 5")
          +6: quantity (numeric)
          +7: unit price
          +8: amount (total value)
          +9: unit weight
          +10: line weight (net weight)
        """
        items = []
        i = data_start
        current_page = 1
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Track page separators
            m_page = re.match(r'^---\s*PAGE\s*(\d+)\s*---', line)
            if m_page:
                current_page = int(m_page.group(1))
                i += 1
                continue
            
            # Skip non-item-number lines
            if not line.isdigit():
                i += 1
                continue
            
            # Verify this is a real item by checking if next line is a stock number
            # Stock numbers are digit strings (6+ digits) or alphanumeric codes like YT-2039
            stock_candidate = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if not stock_candidate or len(stock_candidate) < 5 or ' ' in stock_candidate:
                i += 1
                continue
            if not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]*$', stock_candidate):
                i += 1
                continue
            
            item_num = line
            
            # Need at least stride-1 more lines for item data
            if i + stride - 1 >= len(lines):
                break
            
            # Extract fields for RS format (item, stock, description, country, hs_code, ...)
            stock_no = lines[i + 1].strip()
            description = lines[i + 2].strip()
            country_code = lines[i + 3].strip().upper()
            hs_code_raw = lines[i + 4].strip()
            
            # Extract leading digits only (HS code may be followed by UOM on same line)
            # Examples: "40082110", "40082110 1 MREEL OF 10", "85176200"
            hs_match = re.match(r'^(\d{6,10})', hs_code_raw)
            if not hs_match:
                i += stride
                continue
            
            hs_code = hs_match.group(1)
            
            # Reject codes starting with "1"
            if hs_code.startswith('1'):
                i += stride
                continue
            
            # Detect if HS code and UOM are on the same line (shifts quantity position down by 1)
            # Pattern: "40082110 1 MREEL OF 10" or "85176200" 
            hs_has_uom = len(hs_code_raw) > len(hs_code) + 1  # Has text after HS code
            qty_offset = 5 if hs_has_uom else 6  # Adjust based on whether UOM is on HS line
            
            # Parse quantity from line i+qty_offset
            quantity_raw = lines[i + qty_offset].strip().replace(',', '')
            try:
                quantity = str(int(float(quantity_raw)))
            except ValueError:
                i += stride
                continue
            
            # Validate quantity
            try:
                qty_val = float(quantity)
                if qty_val <= 0 or qty_val > 100000:
                    i += stride
                    continue
            except ValueError:
                i += stride
                continue
            
            # Adjust remaining field offsets based on whether UOM was on HS line
            # If UOM is on HS line, quantity moves up by 1, so all other fields shift up
            offset_adjust = qty_offset - 6  # Will be -1 if HS has UOM, 0 otherwise
            
            # Parse monetary values (unit price, total value)
            unit_price_idx = i + 7 + offset_adjust
            total_value_idx = i + 8 + offset_adjust
            unit_weight_idx = i + 9 + offset_adjust
            net_weight_idx = i + 10 + offset_adjust
            
            unit_value = self._parse_monetary_value(lines[unit_price_idx].strip()) if unit_price_idx < len(lines) else ''
            total_value = self._parse_monetary_value(lines[total_value_idx].strip()) if total_value_idx < len(lines) else ''
            
            # Weights
            unit_weight = lines[unit_weight_idx].strip().replace(',', '.') if unit_weight_idx < len(lines) else ''
            net_weight = lines[net_weight_idx].strip().replace(',', '.') if net_weight_idx < len(lines) else ''
            
            # For RS structured format, only check for footer/header lines.
            # HS code, stock number, and quantity are already structurally validated above.
            if not description or len(description.strip()) < 2:
                i += stride
                continue
            combined_text = (stock_no + ' ' + description + ' ' + hs_code).lower()
            if any(m in combined_text for m in ['bank details:', 'please remit', 'total invoice',
                                                 'grand total', 'payment terms', 'account number']):
                break
            
            # Pad HS code to proper length
            hs_code = self._pad_hs_code(hs_code, pad_to_10)
            
            # Calculate confidence
            confidence = 0.8  # Tabular format with inline description is reliable
            if quantity and quantity != "1":
                confidence += 0.05
            if total_value:
                confidence += 0.05
            if country_code and len(country_code) == 2:
                confidence += 0.05
            
            items.append({
                "item_number": item_num,
                "stock_number": stock_no,
                "description": description,
                "quantity": quantity,
                "uom": "EA",  # UOM is embedded in line i+5 but we use EA as default
                "unit_value": unit_value,
                "total_value": total_value,
                "currency": "GBP",
                "commodity_code": hs_code,
                "country_of_origin": country_code,
                "unit_weight": unit_weight,
                "net_weight": net_weight,
                "pages": [current_page],
                "_page": current_page,
                "confidence": round(min(confidence, 1.0), 2),
                "needs_review": False,
                "raw_text": f"{item_num} {stock_no} {description}"
            })
            
            # Find next item dynamically (stride may vary due to line breaks)
            # Search for the next item number (should be item_num + 1)
            next_item_num = str(int(item_num) + 1)
            found_next = False
            
            # Search within expected range (stride +/- 2 lines for variations)
            search_max = i + stride + 3
            for j in range(i + 1, min(search_max, len(lines))):
                if lines[j].strip() == next_item_num:
                    # Verify this is a real item: next line must be a valid stock number
                    nxt = lines[j + 1].strip() if j + 1 < len(lines) else ''
                    if nxt and len(nxt) >= 5 and ' ' not in nxt and re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]*$', nxt):
                        i = j
                        found_next = True
                        break
            
            if not found_next:
                # If exact next item not found, check if we should continue
                # by looking for any item number > current with valid stock number
                for j in range(i + 1, min(i + stride + 5, len(lines))):
                    test_line = lines[j].strip()
                    if test_line.isdigit():
                        try:
                            test_num = int(test_line)
                            if 1 <= test_num <= 99 and test_num > int(item_num):
                                # Verify this is a real item: next line must be a valid stock number
                                nxt2 = lines[j + 1].strip() if j + 1 < len(lines) else ''
                                if nxt2 and len(nxt2) >= 5 and ' ' not in nxt2 and re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]*$', nxt2):
                                    i = j
                                    found_next = True
                                    break
                        except (ValueError, TypeError):
                            pass
                
                if not found_next:
                    i += stride
        
        return items

    def _match_product_info_descriptions(self, lines: List[str], items: List[Dict]) -> List[Dict]:
        """
        Match descriptions from the Product Information section to line items.

        ATI invoices list descriptions GROUPED BY PRODUCT TYPE, not in item order,
        so positional matching is incorrect.  This method uses stock-number fragment
        scoring as the primary strategy:

          1. For every item that has a stock number, score every description by how
             many tokens from the stock number appear inside the description text.
             Assign the best-scoring description (above a confidence threshold) and
             mark it as used.

          2. Remaining items (no stock number, or no confident token match) receive
             the leftover descriptions in document order — a reasonable fallback for
             items whose descriptions can't be disambiguated by stock code alone.

          3. Any item still without a description falls back to its stock number.

        This approach is robust to re-ordering in the Product Info section and does
        not require a hardcoded list of brand names.
        """
        all_descs = self._collect_all_descriptions(lines)  # List[(page_num, text)]
        if not all_descs:
            for item in items:
                stock = (item.get('stock_number') or '').strip()
                item['description'] = stock
            return items

        # --- Rebalance: fix page-overflow descriptions ---------------------------
        # When a description from page N-1 overflows into the Product Information
        # section printed on page N, the description count per page won't match
        # the item count.  Detect this and reassign overflow descriptions by
        # scoring them against items on the short page.
        from collections import Counter as _Ctr
        _desc_pg_cnt: Dict[int, int] = dict(_Ctr(pg for pg, _ in all_descs))
        _item_pg_cnt: Dict[int, int] = dict(_Ctr(it.get('_page', 1) for it in items))

        all_pages = sorted(set(list(_desc_pg_cnt.keys()) + list(_item_pg_cnt.keys())))
        for pg in all_pages:
            excess = _desc_pg_cnt.get(pg, 0) - _item_pg_cnt.get(pg, 0)
            if excess <= 0:
                continue
            # Check if the previous page is short on descriptions
            prev_pg = pg - 1
            if prev_pg not in _item_pg_cnt:
                continue
            shortage = _item_pg_cnt.get(prev_pg, 0) - _desc_pg_cnt.get(prev_pg, 0)
            if shortage <= 0:
                continue

            moves_needed = min(excess, shortage)
            prev_items = [it for it in items if it.get('_page') == prev_pg]

            # Score each description on this page against items on the previous page
            candidates: List[tuple] = []
            for di, (d_pg, desc) in enumerate(all_descs):
                if d_pg != pg:
                    continue
                best_sc = 0.0
                for it in prev_items:
                    stock = (it.get('stock_number') or '').strip()
                    if not stock:
                        continue
                    sc = self._stock_desc_score(stock, desc)
                    if sc > best_sc:
                        best_sc = sc
                if best_sc > 0:
                    candidates.append((best_sc, di))

            candidates.sort(reverse=True)
            for _, di in candidates[:moves_needed]:
                # Reassign this description to the previous page
                all_descs[di] = (prev_pg, all_descs[di][1])
                _desc_pg_cnt[pg] -= 1
                _desc_pg_cnt[prev_pg] = _desc_pg_cnt.get(prev_pg, 0) + 1

        used = [False] * len(all_descs)

        # --- Pass 1: score-based matching via stock number token overlap ----------
        # Phase A: same-page matches first (preserves per-page description counts)
        for item in items:
            stock = (item.get('stock_number') or '').strip()
            if not stock:
                continue
            item_page = item.get('_page', 1)

            best_idx = -1
            best_score = 0.25  # minimum confidence to accept a match

            for di, (pg, desc) in enumerate(all_descs):
                if used[di] or pg != item_page:
                    continue
                score = self._stock_desc_score(stock, desc)
                if score > best_score:
                    best_score = score
                    best_idx = di

            if best_idx >= 0:
                used[best_idx] = True
                item['description'] = f"{stock} - {all_descs[best_idx][1]}"
                item['_desc_matched'] = True

        # Phase B: cross-page matches for items still unmatched (higher threshold)
        for item in items:
            if item.get('_desc_matched'):
                continue
            stock = (item.get('stock_number') or '').strip()
            if not stock:
                continue

            best_idx = -1
            best_score = 0.49  # higher bar for cross-page to avoid false positives

            for di, (pg, desc) in enumerate(all_descs):
                if used[di]:
                    continue
                score = self._stock_desc_score(stock, desc)
                if score > best_score:
                    best_score = score
                    best_idx = di

            if best_idx >= 0:
                used[best_idx] = True
                item['description'] = f"{stock} - {all_descs[best_idx][1]}"
                item['_desc_matched'] = True

        # --- Pass 2: per-page positional fallback for remaining items ----------
        # Items whose stock codes didn't score-match any description receive
        # leftover descriptions from the SAME PAGE in document order.  Per-page
        # alignment is much better than global because descriptions on each page
        # roughly follow the item order on that page (despite product-type
        # grouping that shuffles some entries).
        remaining_by_page: Dict[int, List[tuple]] = {}
        for i, (pg, d) in enumerate(all_descs):
            if not used[i]:
                remaining_by_page.setdefault(pg, []).append((pg, d))

        unmatched_by_page: Dict[int, List[Dict]] = {}
        for item in items:
            if not item.get('_desc_matched'):
                pg = item.get('_page', 1)
                unmatched_by_page.setdefault(pg, []).append(item)

        for pg in sorted(set(list(remaining_by_page.keys()) + list(unmatched_by_page.keys()))):
            pg_items = unmatched_by_page.get(pg, [])
            pg_descs = remaining_by_page.get(pg, [])
            for item, (_, desc) in zip(pg_items, pg_descs):
                stock = (item.get('stock_number') or '').strip()
                if stock:
                    item['description'] = f"{stock} - {desc}"
                else:
                    item['description'] = desc

        # --- Pass 3: same-page HS-heading description swap -----------------------
        # Before majority correction, detect items whose description doesn't fit
        # their HS heading group and swap with a better-fitting description from
        # an item *on the same page* in a different heading.
        #
        # PAGE RESTRICTION is critical — items on different pages have unrelated
        # descriptions, and cross-page swaps cause regressions.
        #
        # This MUST run before stock-prefix majority correction (Pass 4), because
        # majority correction can overwrite the very description we need to
        # rescue.  E.g. "Hexagon key L-wrench" positionally assigned to a
        # "631415" socket item would be overwritten to "1/4 Socket" by majority
        # vote before we can swap it to the correct HS 8205 item.

        import re as _hs_re

        def _kw_set(text: str) -> set:
            """Extract meaningful lowercase keywords (≥3 chars)."""
            ignore = {'for', 'the', 'and', 'with', 'set', 'type', 'din',
                       'parts', 'series', 'metal', 'diameter', 'overall',
                       'drill', 'bit', 'from', 'that', 'this', 'not'}
            words = _hs_re.findall(r'[a-z]{3,}', text.lower())
            return {w for w in words if w not in ignore}

        def _desc_text(item: Dict) -> str:
            """Return just the description part after 'stock - '."""
            d = item.get('description', '')
            return d.split(' - ', 1)[1] if ' - ' in d else d

        from collections import defaultdict as _dd4

        # Group items by (page, HS heading) so swaps stay within the same page
        page_heading_groups: Dict[tuple, List[int]] = _dd4(list)
        for idx, item in enumerate(items):
            hs = (item.get('commodity_code') or '')[:4]
            pg = item.get('_page', 1)
            if hs:
                page_heading_groups[(pg, hs)].append(idx)

        # Also build a per-page index of ALL items for swap candidate search
        page_items_idx: Dict[int, List[int]] = _dd4(list)
        for idx, item in enumerate(items):
            page_items_idx[item.get('_page', 1)].append(idx)

        swapped: set = set()

        # Process SMALLEST heading groups first — smaller groups are more
        # focused and their swap candidates are more likely to be correct.
        # Large groups (e.g. heading 8204 with 12 socket items) have diverse
        # sibling keywords that can steal descriptions from smaller groups.
        sorted_groups = sorted(page_heading_groups.items(),
                               key=lambda kv: len(kv[1]))

        for (pg, hs), indices in sorted_groups:
            if len(indices) < 2:
                continue

            for idx in indices:
                if idx in swapped:
                    continue
                my_kws = _kw_set(_desc_text(items[idx]))
                # Keywords from OTHER items in the same heading+page group
                sibling_kws = set()
                for other in indices:
                    if other != idx:
                        sibling_kws |= _kw_set(_desc_text(items[other]))
                overlap = len(my_kws & sibling_kws)
                if overlap > 0:
                    continue  # description shares keywords with siblings → OK

                # This item has ZERO keyword overlap with same-heading siblings.
                best_swap = -1
                best_gain = 0
                for oidx in page_items_idx.get(pg, []):
                    if oidx in swapped or oidx in indices:
                        continue
                    other_dtxt_kws = _kw_set(_desc_text(items[oidx]))
                    gain = len(other_dtxt_kws & sibling_kws)
                    if gain < 2:
                        continue

                    # Bidirectional check: only swap if the candidate's
                    # description is ALSO misplaced in its own heading group
                    # (zero overlap with its own siblings).  This prevents
                    # stealing correctly-placed descriptions.
                    o_hs = (items[oidx].get('commodity_code') or '')[:4]
                    o_pg = items[oidx].get('_page', 1)
                    o_group = page_heading_groups.get((o_pg, o_hs), [])
                    if len(o_group) >= 2:
                        o_sibling_kws = set()
                        for sib in o_group:
                            if sib != oidx:
                                o_sibling_kws |= _kw_set(_desc_text(items[sib]))
                        if len(other_dtxt_kws & o_sibling_kws) > 0:
                            continue  # candidate fits its own group → don't steal

                    if gain > best_gain:
                        best_gain = gain
                        best_swap = oidx

                if best_swap >= 0:
                    my_dtxt = _desc_text(items[idx])
                    their_dtxt = _desc_text(items[best_swap])
                    my_stock = (items[idx].get('stock_number') or '').strip()
                    their_stock = (items[best_swap].get('stock_number') or '').strip()
                    items[idx]['description'] = (
                        f"{my_stock} - {their_dtxt}" if my_stock else their_dtxt)
                    items[best_swap]['description'] = (
                        f"{their_stock} - {my_dtxt}" if their_stock else my_dtxt)
                    swapped.add(idx)
                    swapped.add(best_swap)

        # --- Pass 4: stock-prefix majority correction ----------------------------
        # Items sharing the same stock code base (e.g. "631020 12", "631020 13",
        # "631020 14" all share base "631020") almost certainly have identical
        # descriptions.  Use majority vote within each prefix group to correct
        # positional mis-assignments caused by the PDF's two-column extraction.
        from collections import Counter, defaultdict as _dd
        prefix_groups: Dict[str, List[Dict]] = _dd(list)
        for item in items:
            stock = (item.get('stock_number') or '').strip()
            if not stock:
                continue
            # Base = first space-delimited token (e.g. "631020" from "631020 12")
            base = stock.split()[0] if ' ' in stock else stock
            if len(base) >= 4:
                prefix_groups[base].append(item)

        # Build a quick set of pool description texts for orphan-guard below.
        _pool_desc_set: Dict[str, int] = {}
        for _, dtxt in all_descs:
            _pool_desc_set[dtxt] = _pool_desc_set.get(dtxt, 0) + 1

        for base, group in prefix_groups.items():
            if len(group) < 3:
                continue
            # Extract the description part (after "stock - ")
            desc_parts = []
            for it in group:
                d = it.get('description', '')
                desc_parts.append(d.split(' - ', 1)[1] if ' - ' in d else d)
            counts = Counter(desc_parts)
            majority, cnt = counts.most_common(1)[0]
            # Need strict majority (>50%)
            if cnt > len(group) // 2:
                for it in group:
                    d = it.get('description', '')
                    cur = d.split(' - ', 1)[1] if ' - ' in d else d
                    if cur != majority:
                        # Guard: don't overwrite if the current description
                        # exists in the pool and would become an orphan
                        # (i.e. no other item would carry it after overwrite).
                        if cur in _pool_desc_set:
                            # Count how many OTHER items carry this description
                            other_cnt = sum(
                                1 for ot in items
                                if ot is not it and (
                                    (ot.get('description', '').split(' - ', 1)[1]
                                     if ' - ' in ot.get('description', '')
                                     else ot.get('description', ''))
                                    == cur
                                )
                            )
                            if other_cnt == 0:
                                # This item is the only holder of a valid pool
                                # description — keep it to avoid orphaning.
                                continue
                        s = (it.get('stock_number') or '').strip()
                        it['description'] = f"{s} - {majority}" if s else majority

        # --- Pass 5: pool reconciliation ----------------------------------------
        # Majority correction can over-assign popular descriptions.  If a
        # description text appears N times in the original pool but more than N
        # items now carry it, the extras must be wrong.  Reassign the excess
        # items using any "orphaned" descriptions (pool entries that no item
        # currently holds).
        pool_counts: Dict[str, int] = {}
        for _, dtxt in all_descs:
            pool_counts[dtxt] = pool_counts.get(dtxt, 0) + 1

        def _item_desc(it: Dict) -> str:
            d = it.get('description', '')
            return d.split(' - ', 1)[1] if ' - ' in d else d

        item_counts: Dict[str, int] = {}
        for it in items:
            dt = _item_desc(it)
            item_counts[dt] = item_counts.get(dt, 0) + 1

        # Collect orphaned descriptions (in pool but zero items have them)
        orphaned: List[str] = []
        seen_pool: Dict[str, int] = {}
        for _, dtxt in all_descs:
            seen_pool[dtxt] = seen_pool.get(dtxt, 0) + 1
            if item_counts.get(dtxt, 0) == 0:
                # Add one copy per missing assignment
                if seen_pool[dtxt] <= (pool_counts[dtxt] - item_counts.get(dtxt, 0)):
                    orphaned.append(dtxt)
        # Deduplicate: only keep as many copies as needed
        orphaned_counts: Dict[str, int] = {}
        for d in orphaned:
            orphaned_counts[d] = orphaned_counts.get(d, 0) + 1
        orphaned_unique: List[str] = []
        for d in orphaned:
            need = pool_counts.get(d, 0) - item_counts.get(d, 0)
            already = sum(1 for x in orphaned_unique if x == d)
            if need > 0 and already < need:
                orphaned_unique.append(d)
        orphaned = orphaned_unique

        if orphaned:
            # Find over-assigned descriptions
            over_assigned: Dict[str, int] = {}
            for dt, ic in item_counts.items():
                pc = pool_counts.get(dt, 0)
                if ic > pc:
                    over_assigned[dt] = ic - pc

            # Build ALL (orphan, excess-item) pairs with scores, then assign
            # globally in best-match-first order so that substring-recoverable
            # truncations are resolved before weaker matches.
            all_pairs: List[tuple] = []
            for oi, orph_desc in enumerate(orphaned):
                ol = orph_desc.lower()
                for idx, it in enumerate(items):
                    dt = _item_desc(it)
                    if dt not in over_assigned or over_assigned.get(dt, 0) <= 0:
                        continue
                    stock = (it.get('stock_number') or '').strip()
                    score = self._stock_desc_score(stock, orph_desc) if stock else 0.0
                    cur_score = self._stock_desc_score(stock, dt) if stock else 0.0
                    # Text-similarity: check BOTH directions (truncation can
                    # go either way depending on where the " - " split cut).
                    dl = dt.lower()
                    text_bonus = 0.0
                    if len(dt) >= 3 and dl in ol:
                        text_bonus = 1.0          # orphan contains current desc
                    elif len(orph_desc) >= 3 and ol in dl:
                        text_bonus = 1.0          # current desc contains orphan
                    advantage = text_bonus + score - cur_score
                    all_pairs.append((advantage, -cur_score, oi, idx, it))

            all_pairs.sort(reverse=True)
            assigned_orphans: set = set()
            _oa_remaining = dict(over_assigned)  # mutable copy

            for advantage, _, oi, idx, it in all_pairs:
                if oi in assigned_orphans:
                    continue
                dt = _item_desc(it)
                if _oa_remaining.get(dt, 0) <= 0:
                    continue
                # Assign orphan to this item
                old_desc = dt
                stock = (it.get('stock_number') or '').strip()
                it['description'] = (
                    f"{stock} - {orphaned[oi]}" if stock else orphaned[oi])
                _oa_remaining[old_desc] -= 1
                assigned_orphans.add(oi)

        # --- Ensure every item has at least a description (stock number fallback) -
        for item in items:
            if not item.get('description'):
                item['description'] = (item.get('stock_number') or '').strip()
            item.pop('_desc_matched', None)  # clean up internal flag

        return items

    # ------------------------------------------------------------------
    def _collect_all_descriptions(self, lines: List[str]) -> List[tuple]:
        """
        Walk every page section in document order and return the individual
        product descriptions found in the Product Information area.

        Returns a list of (page_num, description_text) tuples so that callers
        can do per-page positional matching.
        """
        all_descriptions: List[tuple] = []

        # Page boundaries: either explicit "Page N of M" lines (from raw pages.json)
        # or the injected "--- PAGE N ---" separators (from parse_job_items).
        page_breaks = [
            i for i, ln in enumerate(lines)
            if re.match(r'^Page \d+ of \d+$', ln.strip())
            or re.match(r'^---\s*PAGE\s*\d+\s*---', ln.strip())
        ]

        for page_idx in range(len(page_breaks)):
            page_start = page_breaks[page_idx]
            page_end = (page_breaks[page_idx + 1]
                        if page_idx + 1 < len(page_breaks) else len(lines))
            page_lines = lines[page_start:page_end]

            # Determine page number from the boundary line
            boundary = page_lines[0].strip()
            m = re.search(r'(\d+)', boundary)
            page_num = int(m.group(1)) if m else (page_idx + 1)

            desc_start = self._find_desc_section_start(page_lines)
            if desc_start is None:
                continue

            # Filter obvious footer / totals lines before splitting
            raw: List[str] = []
            for ln in page_lines[desc_start:]:
                s = ln.strip()
                if not s:
                    continue
                if any(tok in s for tok in
                       ['Invoice Total', 'Bank:', 'Sort code:', 'IBAN:', 'BIC:']):
                    continue
                if re.match(r'^Page \d+ of \d+$', s):
                    continue
                # Skip page-separator markers injected by parse_job_items
                if re.match(r'^---\s*PAGE', s):
                    continue
                # Skip bare currency codes and invoice-total amounts
                if s in ('GBP', 'USD', 'EUR') or re.match(r'^[\d,]+\.\d{2}$', s):
                    continue
                raw.append(ln)   # keep original (trailing spaces matter for wrapping)

            for desc_text in self._split_desc_lines(raw):
                all_descriptions.append((page_num, desc_text))

        return all_descriptions

    def _find_desc_section_start(self, page_lines: List[str]) -> Optional[int]:
        """
        Return the line index (within page_lines) where the description section
        begins, or None if no description section is detected on this page.
        """
        # Explicit header used on the first page of ATI invoices
        for i, line in enumerate(page_lines):
            if 'PRODUCT INFORMATION LIST' in line:
                return i + 1

        # Fallback: find the last line that looks like item-table data, then
        # descriptions start immediately after it.
        valid_countries = {
            'CN', 'UK', 'GB', 'DE', 'US', 'JP', 'KR', 'TW', 'IT', 'FR',
            'ES', 'NL', 'BE', 'PL', 'SE', 'AU', 'SG', 'HK', 'IN', 'MY',
            'TH', 'VN', 'CH', 'IE', 'PT', 'NO', 'FI', 'DK', 'AT', 'HU',
        }
        last_item_line = 0
        for i, line in enumerate(page_lines[1:], 1):
            s = line.strip()
            if not s:
                continue
            if (re.match(r'^\d+(\.\d+)?$', s)
                    or re.match(r'^\d{1,3}(,\d{3})*(\.\d+)?$', s)
                    or s.upper() in valid_countries
                    or re.match(r'^\d+\s+OF\s+\d+$', s, re.IGNORECASE)
                    or re.match(r'^\d{6,10}$', s)
                    or 'Invoice Total' in line
                    or 'IBAN:' in line):
                last_item_line = i

        return last_item_line + 1 if last_item_line > 10 else None

    def _line_is_complete(self, raw_line: str) -> bool:
        """
        Return True when *raw_line* ends a description (the next line is NOT a
        wrapped continuation of it).

        Rules:
          1. No trailing space → the line is naturally complete.
          2. Trailing space + last visible token is all-uppercase, all-digits,
             or ends with only uppercase letters (e.g. "PRESSURE", "32MTR",
             "M/24") → continuation (PDF wrapped a mid-sentence line).
          3. Trailing space + last token is a connector word or bare symbol → continuation.
          4. Otherwise (last token is mixed/lowercase, e.g. "Core", "Sick",
             "Ink", "drive", or "=") → complete (natural end of description).
        """
        stripped = raw_line.rstrip() if raw_line else ''
        if not stripped:
            return True

        # Trailing hyphen (word break across lines, e.g. "to 3-" + "Pin") → continuation
        if stripped.endswith('-'):
            return False

        if raw_line == stripped:
            return True  # no trailing space → complete

        last_tok = stripped.rsplit(None, 1)
        if not last_tok:
            return True
        tok = last_tok[-1]

        # All-uppercase / all-digit / ends-with-uppercase-only token → continuation
        if re.match(r'^[A-Z0-9][A-Z0-9/\-]*$', tok):
            return False

        # Token ending with comma means we're mid-list (e.g. "LOCKING,", "Diameter,") → continuation
        if tok.endswith(','):
            return False

        # Common connector words that appear mid-sentence → continuation
        if tok.lower() in {'x', 'of', 'for', 'and', 'or', 'with',
                            'to', 'a', 'the', 'in', 'on', 'as', 'at'}:
            return False

        # Any other token (mixed case, lowercase, symbol like "=") → complete
        return True

    def _split_desc_lines(self, lines: List[str]) -> List[str]:
        """
        Split a flat list of raw text lines into individual product descriptions.

        Key insight: PDF text extraction preserves trailing whitespace on lines
        that are *wrapped* (i.e. continue onto the next line in the original).
        A line with NO trailing space is a natural end-of-sentence/paragraph.

        Rule:
          • If _line_is_complete(previous line) is False, the current line is a
            continuation → same description.
          • Otherwise the current line starts a new description — UNLESS it
            matches known mid-sentence continuation patterns (generic technical
            spec words that never open a description).
          • Additional split trigger: if the current line's first token is the
            same as the first token of the current description (same product
            code repeated), force a new description regardless of trailing space.
        """
        # Generic continuation-phrase patterns (not invoice-specific brand names).
        # These words/phrases genuinely never begin a stand-alone product description.
        CONTINUATION = re.compile(
            r'^(model\b|measuring\b|case\b|nominal\b|scale\b|range\b|connection\b|'
            r'location\b|accuracy\b|class\b|filling\b|window\b|dial\b|standard\b|'
            r'color\b|manufacturer\b|mark\b|safety\b|capillaries\b|thermometers\b|'
            r'for type\b|degree\b|lenght\b|celsius\b|stainless\b|engraved\b|'
            r'sequential\b|aluminium\b|ethernet\b|faceplate\b|modular\b|contact\b|'
            r'blocks\b|round\b|oper\b|lamps\b|travel\b|nitrite\b|thio\b|tfe\b|'
            r'ink ribbon\b|strips\b|asy\b|euro\b|type\s*\)|'
            r'bit\b|overall\b|diameter\b|connectors\b)',
            re.IGNORECASE,
        )

        descriptions: List[str] = []
        current: List[str] = []

        for raw_line in lines:
            s = raw_line.strip()
            if not s:
                continue

            is_new = False
            if current:
                prev_raw = current[-1]

                # If the previous line is "complete" (no mid-sentence wrap),
                # the current line may start a new description.
                if self._line_is_complete(prev_raw):
                    if re.match(r'^[A-Z0-9(]', s) and not CONTINUATION.match(s):
                        is_new = True
                    # Multi-word line starting with lowercase is very likely a
                    # NEW description, not a continuation.  Single lowercase
                    # words ("coated", "point") are true continuations, but
                    # phrases like "wobble fixed extension bar 1/4 dr" clearly
                    # standalone.  Only apply when the previous line has NO
                    # trailing whitespace (unambiguously complete); trailing
                    # space means the PDF indicated wrapping.
                    elif (len(s.split()) >= 4
                          and not CONTINUATION.match(s)
                          and prev_raw == prev_raw.rstrip()):
                        is_new = True

                # Extra trigger: same first token as the description's opening
                # line means a new description starts (e.g. "6491X 16mm…" then
                # "6491X 25mm…" — two different products with the same prefix).
                if not is_new and current:
                    desc_first = current[0].strip().split()[0] if current[0].strip() else ''
                    this_first = s.split()[0] if s else ''
                    if (desc_first and this_first == desc_first
                            and len(desc_first) >= 3
                            and re.match(r'^[A-Z0-9(]', s)
                            and not CONTINUATION.match(s)):
                        is_new = True

            if is_new:
                desc = ' '.join(ln.strip() for ln in current).strip()
                if len(desc) > 3:
                    descriptions.append(desc)
                current = []

            current.append(raw_line)

        if current:
            desc = ' '.join(ln.strip() for ln in current).strip()
            if len(desc) > 3:
                descriptions.append(desc)

        return descriptions

    def _stock_desc_score(self, stock: str, desc: str) -> float:
        """
        Return a confidence score [0, 1] for how well *stock* matches *desc*.

        Strategy:
          • Exact stock number found inside description → 1.0 (certain match).
          • Tokenise the stock number on delimiter characters AND on alpha↔digit
            boundaries.  Track which tokens came from delimiter-splitting (standalone
            tokens like "250" in "706145 250") versus from alpha↔digit splitting
            (sub-parts like "410" from "410A").
          • Pure-alphabetic tokens (≥3 chars) and pure-numeric tokens (≥3 digit
            chars) both contribute to the score.
          • Delimiter-split 3-digit numbers use a STRICT boundary so that "250"
            doesn't falsely match "250V" or "100" doesn't match "0-100".
          • Alpha-digit-split 3-digit numbers use a LOOSE boundary so that "410"
            (from "410A") correctly matches "410A Series" in a description.
          • Long numbers (≥4 digit chars) use simple substring matching.
        """
        if not stock or not desc:
            return 0.0

        desc_up = desc.upper()

        # Exact inclusion → perfect score
        if stock.upper() in desc_up:
            return 1.0

        # Build two token sets, tracking source:
        #   delim_toks  – tokens from splitting on whitespace / - / \ , 
        #   adi_toks    – sub-tokens created by alpha↔digit boundary splitting
        delim_toks: set = set()
        adi_toks: set = set()
        for part in re.split(r'[\s\-/\\,]', stock):
            if part:
                delim_toks.add(part)
                subs = re.split(r'(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])', part)
                for sub in subs:
                    if sub and sub != part:
                        adi_toks.add(sub)

        all_toks = delim_toks | adi_toks

        # Classify tokens for scoring
        alpha_toks = [t for t in all_toks if re.match(r'^[A-Za-z]{3,}$', t)]

        num_long   = [t for t in all_toks
                      if re.match(r'^\d[\d.]*$', t)
                      and sum(c.isdigit() for c in t) >= 4]

        # 3-digit numerics from delimiter split: USE STRICT BOUNDARY
        #   "250" should not match "250V", "100" should not match "0-100"
        num_short_strict = [t for t in delim_toks
                            if re.match(r'^\d[\d.]*$', t)
                            and sum(c.isdigit() for c in t) == 3]

        # 3-digit numerics from alpha-digit split: USE LOOSE BOUNDARY
        #   "410" (from "410A") should still match "410A Series" in a description
        num_short_loose  = [t for t in adi_toks - delim_toks
                            if re.match(r'^\d[\d.]*$', t)
                            and sum(c.isdigit() for c in t) == 3]

        total = (len(alpha_toks) + len(num_long)
                 + len(num_short_strict) + len(num_short_loose))
        if total == 0:
            return 0.0

        hits = 0
        for t in alpha_toks:
            if t.upper() in desc_up:
                hits += 1
        for t in num_long:
            if t in desc:
                hits += 1
        for t in num_short_strict:
            # Strict: not preceded by digit/minus, not followed by alphanumeric
            # Prevents "250" matching "250V" and "100" matching "0-100 DEGREE"
            if re.search(r'(?<![0-9\-])' + re.escape(t) + r'(?![a-zA-Z0-9])', desc):
                hits += 1
        for t in num_short_loose:
            # Loose: only require no adjacent digits
            # Allows "410" (from "410A") to match "BETA 410A Series"
            if re.search(r'(?<!\d)' + re.escape(t) + r'(?!\d)', desc):
                hits += 1

        return hits / total
    
    def _is_valid_item(self, description: str, stock_no: str = "", quantity: str = "", total_value: str = "") -> bool:
        """
        Comprehensive validation to determine if extracted data represents a real item.
        Returns True only if this looks like a genuine product line item.
        """
        
        # Rule 1: Must have a description
        if not description or len(description.strip()) < 3:
            return False
        
        desc_lower = description.lower()
        
        # Rule 2: HARD REJECT - Contains item count indicator "(X items)"
        if re.search(r'\(\d+\s+items?\)', description, re.IGNORECASE):
            return False
        
        # Rule 3: HARD REJECT - Contains summary/header keywords
        reject_keywords = [
            'rohs compliant',
            'net weight:',
            'shipping marks',
            'container number',
            'no. and kind',
            'bank details',
            'goods value',
            'freight',
            'invoice total',
            'case number',
            'rumber and wo',  # OCR for "Number and Wo"
            'paressens',  # OCR artifacts from packing lists
        ]
        
        if any(keyword in desc_lower for keyword in reject_keywords):
            return False
        
        # Rule 4: HARD REJECT - Table column headers
        header_patterns = [
            r'description.*quantity',
            r'quantity.*unit\s*price',
            r'unit\s*price.*amount',
            r'item.*description.*qty',
            r'^tem\s+description',  # OCR error for "Item Description"
        ]
        
        if any(re.search(pattern, desc_lower) for pattern in header_patterns):
            return False
        
        # Rule 5: HARD REJECT - Starts with asterisks/special chars
        if re.match(r'^[\*\-=]{2,}', description.strip()):
            return False
        
        # Rule 6: Check stock number for keywords (summaries often have text in stock field)
        if stock_no:
            stock_reject = ['rohs', 'compliant', 'net weight', 'shipping', 'marks', 'container', 'tem description']
            if any(word in stock_no.lower() for word in stock_reject):
                return False
        
        # Rule 7: Description must have actual letters (not all symbols/numbers)
        letter_count = len(re.findall(r'[a-zA-Z]', description))
        if letter_count < 3:
            return False
        
        # Rule 8: Description shouldn't be mostly punctuation
        punct_count = len(re.findall(r'[^\w\s]', description))
        if punct_count > len(description) * 0.4:  # More than 40% punctuation
            return False
        
        # Rule 9: If quantity provided, must be reasonable
        if quantity:
            try:
                qty = float(quantity)
                if qty <= 0 or qty > 100000:
                    return False
            except (ValueError, TypeError):
                return False
        
        # Rule 10: Description shouldn't be all caps abbreviations (likely headers)
        # BUT: Accept technical specifications with mixed alphanumeric (e.g., "5MM", "240V", "RELAY24VDC")
        # Example REJECT: "ITEM QTY UOM PRICE"
        # Example ACCEPT: "8A DPCO 5MM RT RELAY24VDC" (technical spec)
        # Example ACCEPT: "HAND PAD 7447 PRO 20 PADS" (has numeric product tokens)
        # Example ACCEPT: "NYLON-INSULATED TWIN CORD" (has hyphenated compound word)
        words = description.split()
        if len(words) >= 3:
            # Extract only pure alphabetic words
            alpha_words = [w for w in words if w.isalpha()]
            # Check if there are any alphanumeric techspec words (contain both letters and digits)
            has_techspec = any(any(c.isdigit() for c in w) and any(c.isalpha() for c in w) for w in words)
            # Check if any word contains a digit or non-alpha punctuation — indicates product content
            has_product_indicator = any(
                any(c.isdigit() for c in w) or any(c in '-./\\' for c in w)
                for w in words
            )

            if alpha_words and not has_techspec and not has_product_indicator:
                # Pure text with no product indicators — apply strict header check
                all_caps_short = all(w.isupper() and len(w) <= 5 for w in alpha_words)
                # Allow if any word (including non-alpha) is >= 5 chars
                has_normal_word = any(len(w) >= 5 for w in words)
                if all_caps_short and not has_normal_word:
                    return False
            elif alpha_words and has_techspec:
                # Technical specification (mixed alphanumeric) — only reject if very short
                # E.g., "8A DPCO 5MM RT" has technical tokens, so accept it
                # Only reject if shortest description or obviously a header pattern
                total_letters = sum(len(re.findall(r'[a-zA-Z]', w)) for w in words)
                if total_letters < 5:
                    return False
        
        # Rule 11: HARD REJECT - Looks like just a part/item number (mostly digits with few letters)
        # Example: "2065470" or "3993135" without meaningful description
        if len(description) <= 15:
            digit_count = len(re.findall(r'\d', description))
            if digit_count > len(description) * 0.6:  # More than 60% digits
                return False
        
        return True
    
    def _parse_horizontal_table(self, lines: List[str], table_start: int, direction: str, page_map: Dict) -> List[Dict]:
        """Parse horizontal table format where all fields are on one row"""
        items = []
        pad_to_10 = (direction.lower() == "import")
        self._seen_items = set()  # Track unique items to avoid duplicates
        
        # Process lines after header - look for rows with item data
        # Tinto format: STOCK_CODE LINE_NUM HS_CODE DESCRIPTION UOM QTY UNIT_PRICE TOTAL CURRENCY
        # E.g.: "134420 1 3822.90.0000 RCMSi Certification Lq'd (Conf) PK 1.00 140.84 0.00 140.84 ROW"
        
        for i in range(table_start + 1, len(lines)):
            line = lines[i].strip()

            if not line:
                continue

            # Item lines may start with either the STOCK_CODE or an item number
            # followed by STOCK_CODE. Support both formats.
            parts = line.split()
            stock_no = None
            line_num = None

            if len(parts) >= 2 and parts[0].isdigit() and re.match(r'^\d{4,7}$', parts[1]):
                # Format: ITEM_NUM STOCK_CODE ...
                line_num = parts[0]
                stock_no = parts[1]
                rest_parts = parts[2:]
            elif re.match(r'^\d{4,7}$', parts[0]):
                # Format: STOCK_CODE ...
                stock_no = parts[0]
                # line number may be next or absent
                line_num = parts[1] if len(parts) > 1 and parts[1].isdigit() else ''
                rest_parts = parts[2:] if line_num else parts[1:]
            else:
                # not a candidate row
                continue

            # candidate parsing
            
            # Find HS code token inside rest_parts (accept contiguous digits or dotted formats)
            # HS code is typically 8 digits like 85444290, 85366990, etc.
            # May be preceded by a 2-letter COO code (e.g., VN, CN, CZ, AT, DE)
            hs_idx = -1
            hs_code_raw = None
            coo_idx = -1
            
            for idx, p in enumerate(rest_parts[:12]):  # Look further ahead
                token = p.strip().replace('.', '').replace(',', '')
                if re.match(r'^\d{6,10}$', token):
                    # Found HS code - check if preceded by COO code
                    hs_idx = idx
                    hs_code_raw = p.strip()
                    # Check if previous token is a 2-letter country code
                    if idx > 0:
                        prev = rest_parts[idx - 1].strip()
                        if re.match(r'^[A-Z]{2}$', prev):
                            # Previous token is a country code - skip it from description
                            coo_idx = idx - 1
                    break

            if hs_idx == -1:
                # No HS token in this candidate row; skip
                continue

            # Description goes up to (but not including) the country code if present, else up to HS code
            desc_end = coo_idx if coo_idx != -1 else hs_idx
            description = ' '.join(rest_parts[:desc_end]).strip()
            after_idx = hs_idx + 1
            
            # Country of origin from the token we identified (or empty)
            coo = rest_parts[coo_idx].strip() if coo_idx != -1 else ""
            
            # Normalize HS code (remove dots/commas)
            hs_code = hs_code_raw.replace('.', '').replace(',', '')
            
            # Reject codes starting with "1"
            if hs_code.startswith('1'):
                continue
            
            # Tokens after HS code may contain unit/qty/prices in various orders.
            tokens_after = rest_parts[after_idx:]

            # UOM: first token that looks like a unit descriptor
            uom = "EA"
            for t in tokens_after[:6]:
                if re.match(r'^(EA|BAG|SET|PK|PCS|UNIT|KG|REEL|EACH|ITEM|PACK|FA|BOX|CASE|MREEL|BAG)$', t, re.IGNORECASE):
                    uom = t.upper()
                    break

            # Extract numeric-looking tokens for qty and prices (with positions)
            num_tokens = []  # list of (idx_in_tokens_after, type, value)
            for idx, t in enumerate(tokens_after):
                # normalize common money formatting
                t_clean = t.replace('$', '').replace('US$', '').replace(',', '')
                if re.match(r'^\d+$', t_clean):
                    num_tokens.append((idx, 'int', t_clean))
                elif re.match(r'^\d+\.\d{1,4}$', t_clean):
                    num_tokens.append((idx, 'float', t_clean))

            quantity = ""
            unit_value = ""
            total_value = ""
            # quantity: prefer integer token NOT immediately preceded by 'OF' (common pack marker)
            qty_candidate = None
            for idx, typ, val in num_tokens:
                if typ == 'int':
                    prev_tok = tokens_after[idx-1].lower() if idx-1 >= 0 else ''
                    if prev_tok != 'of' and prev_tok != 'mreel' and prev_tok != 'pack' and prev_tok != 'bag':
                        try:
                            qv = int(val)
                            if 0 < qv <= 100000:
                                qty_candidate = str(qv)
                                break
                        except Exception:
                            continue
            if not qty_candidate and num_tokens:
                # fallback to last integer token
                ints = [val for idx, typ, val in num_tokens if typ == 'int']
                if ints:
                    qty_candidate = ints[-1]
            quantity = qty_candidate or ""

            # unit_value and total_value: first two float-looking tokens (or integers if floats absent)
            floats = [val for idx, typ, val in num_tokens if typ == 'float']
            ints = [val for idx, typ, val in num_tokens if typ == 'int']
            if floats:
                unit_value = floats[0]
                total_value = floats[1] if len(floats) > 1 else (floats[0] if floats else '')
            else:
                # fallback to integers (rare) - use last as total
                if ints:
                    unit_value = ints[0]
                    total_value = ints[-1]

            unit_weight = ''
            net_weight = ''

            # Use validator to reject non-product lines
            if not self._is_valid_item(description, stock_no, quantity, total_value):
                continue
            
            # Get currency and VAT code from line
            # Format: [...prices...] TOTAL_PRICE VAT_CODE
            # The last field is VAT code (ROW, GBP, etc), not currency
            # Currency is typically indicated elsewhere (e.g., £ symbol means GBP)
            currency = "GBP"  # Default to GBP (UK-based company, invoice has £ symbol)
            vat_code = ""
            
            if tokens_after:
                # Last token may be a VAT/currency code (short token)
                vat_code = tokens_after[-1] if len(tokens_after[-1]) <= 3 else ""
            
            # Note: Country of origin already extracted earlier (coo variable set when parsing HS code)
            # No need to search next lines
            
            # Avoid duplicate ITEMS (same line number + stock), not duplicate descriptions
            # Different items can have identical product descriptions (packs of same product)
            item_key = (stock_no, line_num, hs_code)
            if not hasattr(self, '_seen_items'):
                self._seen_items = set()
            
            if item_key in self._seen_items:
                continue
            self._seen_items.add(item_key)
            
            # Pad HS code
            hs_code = self._pad_hs_code(hs_code, pad_to_10)
            
            # Calculate confidence
            confidence = 0.7  # Tabular format is reliable
            if quantity and quantity != "1":
                confidence += 0.1
            if total_value:
                confidence += 0.1
            if description and len(description) > 5:
                confidence += 0.05
            if coo:
                confidence += 0.05
            
            items.append({
                "line_number": line_num,
                "stock_number": stock_no,
                "description": description,
                "quantity": quantity,
                "uom": uom,
                "unit_value": unit_value,
                "total_value": total_value,
                "currency": currency,
                "vat_code": vat_code,
                "commodity_code": hs_code,
                "country_of_origin": coo,
                "unit_weight": unit_weight,
                "net_weight": net_weight,
                "hs_code": hs_code,  # Add explicit hs_code field
                "pages": [page_map.get(sum(len(l) + 1 for l in lines[:i]), 1)],
                "confidence": round(min(confidence, 1.0), 2),
                "needs_review": confidence < 0.7,
                "raw_text": line[:200]
            })
        
        return items
    
    def _parse_monetary_value(self, value: str) -> str:
        """
        Parse monetary value string, handling thousands separators.
        ATI invoices use comma as thousands separator (1,230.00 = 1230.00)
        """
        if not value:
            return value
        
        # If value has both comma and dot, comma is thousands separator
        # e.g., "1,230.00" -> "1230.00"
        if ',' in value and '.' in value:
            return value.replace(',', '')
        
        # If value has only comma and it's followed by 3 digits at end, it's thousands
        # e.g., "1,230" -> "1230"
        if ',' in value:
            parts = value.split(',')
            if len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit():
                return value.replace(',', '')
            # Otherwise comma might be decimal (European format)
            # e.g., "1,23" -> "1.23"
            return value.replace(',', '.')
        
        return value
    
    def _country_to_iso(self, country: str) -> str:
        """Convert country name or code to ISO 2-letter code"""
        country_map = {
            'CN': 'CN', 'CHINA': 'CN',
            'UK': 'GB', 'UNITED KINGDOM': 'GB', 'GB': 'GB',
            'DE': 'DE', 'GERMANY': 'DE',
            'US': 'US', 'USA': 'US', 'UNITED STATES': 'US',
            'JP': 'JP', 'JAPAN': 'JP',
            'KR': 'KR', 'KOREA': 'KR', 'SOUTH KOREA': 'KR',
            'TW': 'TW', 'TAIWAN': 'TW',
            'IT': 'IT', 'ITALY': 'IT',
            'FR': 'FR', 'FRANCE': 'FR',
            'ES': 'ES', 'SPAIN': 'ES',
            'NL': 'NL', 'NETHERLANDS': 'NL',
            'BE': 'BE', 'BELGIUM': 'BE',
            'PL': 'PL', 'POLAND': 'PL',
            'SE': 'SE', 'SWEDEN': 'SE',
            'CZ': 'CZ', 'CZECH': 'CZ', 'CZECH REPUBLIC': 'CZ',
            'AT': 'AT', 'AUSTRIA': 'AT',
            'DK': 'DK', 'DENMARK': 'DK',
            'HU': 'HU', 'HUNGARY': 'HU',
            'IN': 'IN', 'INDIA': 'IN',
            'MX': 'MX', 'MEXICO': 'MX',
            'CA': 'CA', 'CANADA': 'CA',
            'AU': 'AU', 'AUSTRALIA': 'AU',
            'SG': 'SG', 'SINGAPORE': 'SG',
            'HK': 'HK', 'HONG KONG': 'HK',
            'MY': 'MY', 'MALAYSIA': 'MY',
            'TH': 'TH', 'THAILAND': 'TH',
            'VN': 'VN', 'VIETNAM': 'VN',
        }
        return country_map.get(country.upper(), country)
    
    def _pad_hs_code(self, hs_code: str, pad_to_10: bool) -> str:
        """Pad HS code to correct length - 8 for export, 10 for import"""
        if pad_to_10:
            # Import: pad to 10 digits
            if len(hs_code) == 6:
                return hs_code + "0000"
            elif len(hs_code) == 7:
                return hs_code + "000"
            elif len(hs_code) == 8:
                return hs_code + "00"
            elif len(hs_code) == 9:
                return hs_code + "0"
        else:
            # Export: pad to 8 digits
            if len(hs_code) == 6:
                return hs_code + "00"
            elif len(hs_code) == 7:
                return hs_code + "0"
        
        return hs_code
    
    def _parse_pattern_format(self, lines: List[str], direction: str, page_map: Dict) -> List[Dict]:
        """Parse invoices using pattern matching (original method)"""
        items = []
        seen_descriptions = set()  # Track descriptions to avoid duplicates
        
        # Keywords that explicitly indicate a commodity/HS code
        hs_keywords = [
            'hs code', 'hs-code', 'hscode', 'hs export code', 'hs import code',
            'commodity code', 'commodity-code', 'commoditycode',
            'tariff code', 'tariff-code', 'tariffcode', 'tariff number',
            'customs code', 'cn code', 'cn8', 'export code', 'import code'
        ]
        
        # Quantity patterns
        qty_patterns = [
            r'(?:qty|quantity|qnty)[\s:]*(\d+(?:\.\d+)?)\s*([a-z]*)',
            r'(\d+(?:\.\d+)?)\s*(ea|each|pcs|pieces|units|items|pc)\b',
            r'(?:^|\s)(\d+)\s+(ea|each|pcs|pieces|units)\b',
        ]
        
        # Value patterns
        value_patterns = [
            r'(?:amount|total|value|price|unit\s*price|cost)[\s:]*[£$€¥]?\s*(\d+[,\d]*\.?\d+)',
            r'[£$€¥]\s*(\d+[,\d]*\.?\d+)',
            r'\b(\d+\.\d{2})\b(?!\s*(?:kg|g|mm|cm|m|ea|pcs))',
        ]
        
        # Weight patterns (net weight)
        weight_patterns = [
            r'(?:net\s*weight|nett\s*weight|n\.?w\.?|weight)[\s:]*([\d,]+\.?\d*)\s*(?:kg|kilograms?)',
            r'([\d,]+\.?\d*)\s*kg\b',
            r'(?:net\s*weight|nett\s*weight|n\.?w\.?|weight)[\s:]*([\d,]+\.?\d*)\s*(?:g|grams?)\b',
            r'([\d,]+\.?\d*)\s*g\b(?!ram)',  # grams but not 'gram' or 'program'
        ]
        
        # Currency pattern
        currency_pattern = r'\b(GBP|USD|EUR|CAD|AUD|CNY|JPY|CHF)\b'
        
        pad_to_10 = (direction.lower() == "import")
        
        # Track if we found ANY commodity codes
        commodity_codes_found = 0
        
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            
            line_lower = line.lower()
            
            # Look for explicit HS code mentions (primary method)
            hs_code_match = re.search(
                r'(?:hs\s*export\s*code|hs\s*import\s*code|hs\s*code|hs\s*code\s*export|hs\s*code\s*import|commodity\s*code|tariff\s*code|customs\s*code)[\s:]*(\d{6,10})',
                line, re.IGNORECASE
            )
            
            # Fallback: Look for standalone 6-10 digit codes (less strict)
            if not hs_code_match:
                # Look for patterns like "738480" or "73848000" that could be HS codes
                # But not preceded/followed by other numbers (to avoid order numbers, SKUs, etc.)
                standalone_match = re.search(r'(?:^|\s)(\d{6,10})(?:\s|$)', line)
                if standalone_match:
                    potential_code = standalone_match.group(1)
                    
                    # Reject if this looks like a postal code, phone number, or address
                    # Postal codes are usually 5-6 digits and appear near address keywords
                    if len(potential_code) < 8:
                        # Check context for address-related keywords
                        if any(keyword in line_lower for keyword in ['almaty', 'address', 'manchester', 'street', 'business center', 'office', 'floor', 'arkwright', 'parsonage']):
                            continue
                    
                    # Additional validation: check if context suggests this is a commodity code
                    context_before = ' '.join(lines[max(0, i-2):i+1])
                    context_after = ' '.join(lines[i:min(len(lines), i+3)])
                    
                    # Look for commodity-related keywords nearby
                    commodity_indicators = [
                        'commodity', 'hs', 'tariff', 'customs', 'harmonized',
                        'classification', 'export code', 'import code',
                        'country of origin', 'c of o', 'coo', 'made in'
                    ]
                    
                    has_indicator = any(indicator in context_before.lower() or indicator in context_after.lower() 
                                       for indicator in commodity_indicators)
                    
                    if has_indicator:
                        hs_code_match = standalone_match
            
            if hs_code_match:
                commodity_codes_found += 1
                hs_code = hs_code_match.group(1)
                
                # Reject HS codes starting with "1" (user's products don't use these)
                if hs_code.startswith('1'):
                    continue
                
                # Pad to appropriate length
                if len(hs_code) == 6:
                    hs_code = hs_code + ("0000" if pad_to_10 else "00")
                elif len(hs_code) == 8:
                    hs_code = hs_code + "00" if pad_to_10 else hs_code
                elif len(hs_code) == 7:
                    hs_code = hs_code + ("000" if pad_to_10 else "0")
                
                # Extract description from line before HS code
                description = None
                
                # Look 1-2 lines back for description
                for j in range(1, 4):
                    if i - j >= 0:
                        prev_line = lines[i - j].strip()
                        # Valid description: not empty, not a header, has substance
                        if prev_line and len(prev_line) > 10:
                            # Skip if it looks like a header or metadata
                            if not re.match(r'^\s*(hs|item|order|shipment|page)', prev_line, re.IGNORECASE):
                                # Check if it has part number + description pattern
                                # Example: "738480 PANEL SKT RED 50 EA 1.1000 55.00"
                                parts = prev_line.split()
                                if len(parts) >= 2:
                                    # First part might be item/part number, take rest as description
                                    # But keep first 50 chars
                                    description = ' '.join(parts[:min(len(parts), 8)])
                                    break
                
                if not description:
                    continue
                
                # Build context from surrounding lines for extracting other fields
                # Include more lines after for country extraction (C of O appears after HS code)
                context_lines = []
                for j in range(max(0, i-3), min(len(lines), i+7)):
                    context_lines.append(lines[j])
                context = ' '.join(context_lines)
                
                # Extract quantity and unit
                quantity = ""
                uom = ""
                for pattern in qty_patterns:
                    match = re.search(pattern, context, re.IGNORECASE)
                    if match:
                        try:
                            quantity = str(int(float(match.group(1))))
                            if len(match.groups()) > 1 and match.group(2):
                                uom = match.group(2).lower()
                            else:
                                uom = "pcs"
                        except (ValueError, IndexError):
                            pass
                        break
                
                # Extract value
                unit_value = ""
                total_value = ""
                currency = "GBP"  # Default from invoice
                
                # Find currency
                curr_match = re.search(currency_pattern, context)
                if curr_match:
                    currency = curr_match.group(1)
                
                # Find values
                found_values = []
                for pattern in value_patterns:
                    for match in re.finditer(pattern, context, re.IGNORECASE):
                        try:
                            val_str = match.group(1).replace(',', '')
                            val = float(val_str)
                            if 0.01 <= val <= 1000000:
                                found_values.append(val)
                        except (ValueError, IndexError):
                            pass
                
                if found_values:
                    found_values = sorted(set(found_values))
                    if len(found_values) >= 2:
                        unit_value = str(found_values[0])
                        total_value = str(found_values[-1])
                    else:
                        total_value = str(found_values[0])
                
                # Extract Country of Origin - improved pattern for multi-word countries
                coo = ""
                # Pattern matches "C of O China" or "C of O United Kingdom" etc.
                coo_match = re.search(r'c\s*of\s*o[\s:]*([a-z][a-z\s]+?)(?=\s*(?:Net Weight|HS export|\d+|$))', context, re.IGNORECASE)
                if coo_match:
                    coo = coo_match.group(1).strip().title()
                
                # If not found in context, try looking in the next few lines after HS code
                if not coo:
                    for j in range(i+1, min(len(lines), i+5)):
                        line_after = lines[j]
                        coo_match = re.search(r'c\s*of\s*o[\s:]*([a-z][a-z\s]+?)(?=\s*(?:Net Weight|HS export|\d+|$))', line_after, re.IGNORECASE)
                        if coo_match:
                            coo = coo_match.group(1).strip().title()
                            break
                
                # Extract net weight
                net_weight = ""
                for pattern in weight_patterns:
                    match = re.search(pattern, context, re.IGNORECASE)
                    if match:
                        try:
                            weight_str = match.group(1).replace(',', '')
                            weight_val = float(weight_str)
                            # If in grams, convert to kg
                            if 'g' in pattern and 'kg' not in pattern:
                                if weight_val > 10:  # Likely grams if > 10
                                    weight_val = weight_val / 1000
                            net_weight = f"{weight_val:.3f}"
                            break
                        except (ValueError, IndexError):
                            pass
                
                # Determine pages (from line position in text)
                line_pos = sum(len(l) + 1 for l in lines[:i])
                pages = [page_map.get(line_pos, 1)]
                
                # Calculate confidence
                confidence = 0.0
                confidence_factors = []
                
                if hs_code:
                    confidence_factors.append(0.3)
                if quantity:
                    confidence_factors.append(0.2)
                if total_value:
                    confidence_factors.append(0.2)
                if description and len(description) > 10:
                    confidence_factors.append(0.2)
                if coo:
                    confidence_factors.append(0.1)
                
                confidence = sum(confidence_factors)
                needs_review = confidence < 0.6 or not quantity or not total_value
                
                # VALIDATION: Use comprehensive validation function
                if not self._is_valid_item(description, "", quantity, total_value):
                    continue
                
                # Check for duplicate descriptions
                desc_normalized = description.strip().lower()
                if desc_normalized in seen_descriptions:
                    continue  # Skip duplicate item
                seen_descriptions.add(desc_normalized)
                
                items.append({
                    "description": description,
                    "quantity": quantity,
                    "uom": uom,
                    "unit_value": unit_value,
                    "total_value": total_value,
                    "currency": currency,
                    "commodity_code": hs_code,
                    "country_of_origin": coo,
                    "net_weight": net_weight,
                    "pages": pages,
                    "confidence": round(confidence, 2),
                    "needs_review": needs_review,
                    "raw_text": context[:200]
                })
        
        return items

