"""
Line Item Parser - Pass 2: Extract structured data from extracted text
Handles multi-page items and uses proven parsing from pdf_extractor.py
"""
import re
from typing import List, Dict, Tuple
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
        
        # Parse items using the proven logic from pdf_extractor.py
        items = self._parse_line_items_proven(all_text, direction, page_map)
        
        return {
            "total_items": len(items),
            "items": items,
            "pages_analyzed": len(pages_data.get("pages", [])),
            "direction": direction
        }
    
    def _parse_line_items_proven(self, text: str, direction: str, page_map: Dict) -> List[Dict]:
        """Use the proven parsing logic with enhanced table detection"""
        items = []
        lines = text.split('\n')
        
        # First, try to detect if this is a tabular invoice format
        # Look for "HS Codes" column header (might be on its own line in vertical format)
        has_table_format = False
        table_start_idx = -1
        
        for i, line in enumerate(lines):
            # Look for HS Codes header line
            if 'HS Codes' in line or 'HS Code' in line:
                # Check if nearby lines (within 10 lines before) have other table headers
                context_start = max(0, i - 10)
                context_lines = ' '.join(lines[context_start:i+1]).lower()
                if any(keyword in context_lines for keyword in ['item', 'stock', 'description', 'unit', 'quantity', 'amount']):
                    has_table_format = True
                    table_start_idx = i

        # If we detected table format, use table parsing
        if has_table_format:
            return self._parse_tabular_format(lines, table_start_idx, direction, page_map)
        
        # Otherwise, use the original pattern-based parsing
        return self._parse_pattern_format(lines, direction, page_map)
    
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
            return self._parse_vertical_table(lines, table_start, direction, page_map)
        else:
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
        
        # Process items in vertical format
        # Each item has fixed number of lines: item#, stock, desc, uom, qty, price, amount, weight, weight, hs, cofo, country
        i = data_start
        while i < len(lines):
            line = lines[i].strip()
            
            # Check if this is an item number (starts a new item)
            if not line.isdigit():
                i += 1
                continue
            
            item_num = line
            
            # Need at least 9 more lines for minimum item data (through HS code)
            if i + 9 >= len(lines):
                break
            
            # Extract fields first
            stock_no = lines[i + 1].strip()
            description = lines[i + 2].strip()
            
            # Check if THIS item is actually a footer line (not just near footer)
            # Footer markers should appear in stock or description fields of non-items
            item_text = (stock_no + ' ' + description).lower()
            if any(marker in item_text for marker in ['bank details:', 'please remit to:', 'total invoice value:', 'grand total:', 'payment terms', 'account number']):
                break
            uom = lines[i + 3].strip().upper()
            quantity = lines[i + 4].strip().replace(',', '.')
            unit_value = lines[i + 5].strip().replace(',', '.')
            total_value = lines[i + 6].strip().replace(',', '.')
            unit_weight = lines[i + 7].strip().replace(',', '.')
            net_weight = lines[i + 8].strip().replace(',', '.')
            hs_code = lines[i + 9].strip()
            
            # Lines i+10 should be "CofO" and i+11 should be country
            coo = ""
            if i + 11 < len(lines):
                if lines[i + 10].strip().lower() == 'cofo':
                    coo = lines[i + 11].strip()
            
            # VALIDATION 1: HS code must be 6-10 digits and NOT start with "1"
            if not re.match(r'^\d{6,10}$', hs_code):
                i += 12  # Skip to next potential item
                continue
            
            # VALIDATION 1b: User's products don't have HS codes starting with "1"
            if hs_code.startswith('1'):
                i += 12  # Skip to next potential item
                continue
            
            # VALIDATION 2: Use comprehensive validation function
            if not self._is_valid_item(description, stock_no, quantity, total_value):
                i += 12  # Skip to next potential item
                continue
            
            # VALIDATION 3: UOM must be valid (but be lenient - some invoices use variations)
            valid_uoms = ['EA', 'BAG', 'SET', 'PCS', 'UNIT', 'UNITS', 'KG', 'REEL', 'EACH', 'ITEM', 'PACK', 'BOX', 'PIECE', 'PC', 'PAIR', 'DOZEN', 'MTR', 'M', 'L', 'LITRE', 'LITER']
            if uom and uom not in valid_uoms:
                # If UOM is present but invalid, skip this item
                i += 12  # Skip to next potential item
                continue
            
            # Pad HS code to proper length
            hs_code = self._pad_hs_code(hs_code, pad_to_10)
            
            # VALIDATION 4: Check for duplicate descriptions
            desc_normalized = description.strip().lower()
            if desc_normalized in seen_descriptions:
                i += 12  # Skip duplicate item
                continue
            seen_descriptions.add(desc_normalized)
            
            # Calculate confidence
            confidence = 0.4  # Base for HS code
            if quantity and quantity != "1":
                confidence += 0.2
            if total_value:
                confidence += 0.2
            if description and len(description) > 5:
                confidence += 0.1
            if coo:
                confidence += 0.1
            
            items.append({
                "description": description,
                "quantity": quantity,
                "uom": uom,
                "unit_value": unit_value,
                "total_value": total_value,
                "currency": "GBP",
                "commodity_code": hs_code,
                "country_of_origin": coo,
                "net_weight": net_weight,
                "pages": [page_map.get(sum(len(l) + 1 for l in lines[:i]), 1)],
                "confidence": round(confidence, 2),
                "needs_review": confidence < 0.7,
                "raw_text": f"{item_num} {stock_no} {description[:50]}"
            })
            
            # Move to next item (skip 12 lines: current + 11 fields)
            i += 12
        
        return items
    
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
        # Example: "ITEM QTY UOM PRICE"
        words = description.split()
        if len(words) >= 3:
            all_caps_short = all(w.isupper() and len(w) <= 5 for w in words if w.isalpha())
            if all_caps_short:
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
        seen_descriptions = set()  # Track descriptions to avoid duplicates
        
        # Process lines after header - look for rows with item data
        for i in range(table_start + 1, len(lines)):
            line = lines[i].strip()
            
            if not line:
                continue
            
            # Skip explicit footer/header lines (not item data)
            line_lower = line.lower()
            if not line[0].isdigit() and any(skip in line_lower for skip in ['bank details:', 'goods value:', 'freight:', 'invoice total:', 'please remit', 'grand total']):
                continue
            
            # Skip CofO lines
            if line.startswith('CofO'):
                continue
            
            # Must start with item number and end with 6-10 digit HS code
            if not re.match(r'^\d+\s', line):
                continue
            
            hs_match = re.search(r'(\d{6,10})\s*$', line)
            if not hs_match:
                continue
            
            hs_code = hs_match.group(1)
            
            # Reject HS codes starting with "1" (user's products don't use these)
            if hs_code.startswith('1'):
                continue
            
            # Remove HS code from line to parse the rest
            line_without_hs = line[:hs_match.start()].strip()
            
            # Split by multiple spaces (2+) to get fields
            fields = re.split(r'\s\s+', line_without_hs)
            
            # Expected fields: item#, stock#, description, UOM, qty, unit_price, amount, unit_weight, line_weight
            if len(fields) < 5:
                continue
            
            item_num = fields[0].strip()
            stock_no = fields[1].strip() if len(fields) > 1 else ""
            
            # Find UOM (EA, BAG, SET, etc.) - this helps split description from numbers
            uom_idx = -1
            uom = "EA"
            for idx, field in enumerate(fields[2:], 2):
                if re.match(r'^(EA|BAG|SET|PCS|UNITS?|KG|REEL|EACH|ITEM|PACK)$', field.strip(), re.IGNORECASE):
                    uom = field.strip().upper()
                    uom_idx = idx
                    break
            
            # Description is between stock_no and UOM
            if uom_idx > 2:
                description = ' '.join(fields[2:uom_idx]).strip()
            else:
                # Fallback: take 3rd field as description
                description = fields[2].strip() if len(fields) > 2 else ""
            
            # After UOM, we expect: qty, unit_price, amount, unit_weight, line_weight
            # Extract all numbers after description
            numbers_text = ' '.join(fields[uom_idx+1:]) if uom_idx > 0 else ' '.join(fields[3:])
            numbers = re.findall(r'\d+[,\.]\d+|\d+', numbers_text)
            numbers = [n.replace(',', '.').replace(' ', '') for n in numbers]
            
            quantity = numbers[0] if len(numbers) > 0 else "1"
            unit_value = numbers[1] if len(numbers) > 1 else ""
            total_value = numbers[2] if len(numbers) > 2 else unit_value
            unit_weight = numbers[3] if len(numbers) > 3 else ""
            net_weight = numbers[4] if len(numbers) > 4 else unit_weight
            
            # VALIDATION: Use comprehensive validation function
            if not self._is_valid_item(description, stock_no, quantity, total_value):
                continue
            
            # Country of origin from next line
            coo = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                coo_match = re.search(r'CofO\s+(.+)', next_line, re.IGNORECASE)
                if coo_match:
                    coo = coo_match.group(1).strip()
                    # Remove trailing numbers/data
                    coo = re.split(r'\s{2,}|\d{2,}', coo)[0].strip()
            
            # Pad HS code
            hs_code = self._pad_hs_code(hs_code, pad_to_10)
            
            # Check for duplicate descriptions
            desc_normalized = description.strip().lower()
            if desc_normalized in seen_descriptions:
                continue  # Skip duplicate item
            seen_descriptions.add(desc_normalized)
            
            # Calculate confidence
            confidence = 0.4
            if quantity and quantity != "1":
                confidence += 0.2
            if total_value:
                confidence += 0.2
            if description and len(description) > 5:
                confidence += 0.1
            if coo:
                confidence += 0.1
            
            items.append({
                "description": description,
                "quantity": quantity,
                "uom": uom,
                "unit_value": unit_value,
                "total_value": total_value,
                "currency": "GBP",
                "commodity_code": hs_code,
                "country_of_origin": coo,
                "net_weight": net_weight,
                "pages": [page_map.get(sum(len(l) + 1 for l in lines[:i]), 1)],
                "confidence": round(confidence, 2),
                "needs_review": confidence < 0.7,
                "raw_text": line[:200]
            })
        
        return items
    
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

