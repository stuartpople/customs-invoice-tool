"""
Multi-format document extraction module
Supports PDF, Excel, and Word documents
"""
from pdf_extractor import extract_text_from_pdf, parse_line_items, extract_invoice_metadata
import pandas as pd
from typing import List, Dict, Callable, Optional, Tuple
import io

# Excel/Word imports
try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


def extract_from_file_with_progress(file_obj, filename: str, trade_direction: str = "export", progress_callback: Optional[Callable] = None):
    """
    Extract data from file with progress updates for OCR.
    
    Args:
        file_obj: File object
        filename: Name of the file
        trade_direction: "export" or "import"
        progress_callback: Function(page_num, total_pages) for progress updates
        
    Returns:
        Tuple of (text, items, metadata)
    """
    file_ext = filename.lower().split('.')[-1]
    
    if file_ext == 'pdf':
        # PDF extraction with progress
        text = extract_text_from_pdf(file_obj, use_ocr=True, progress_callback=progress_callback)
        
        # Parse items and metadata
        items = parse_line_items(text, direction=trade_direction)
        metadata = extract_invoice_metadata(text)
        
        # Add CDS defaults to metadata based on trade direction
        if trade_direction.lower() == 'import':
            metadata['cpc_code'] = '4000'  # Free circulation (default for imports)
            metadata['valuation_method'] = '1'  # Transaction value (Method 1)
        else:  # export
            metadata['cpc_code'] = '1000'  # Permanent export (default for exports)
        
        return text, items, metadata
    else:
        # Other formats don't need progress callbacks
        return extract_from_file(file_obj, filename, trade_direction)


def extract_from_excel(file_obj, trade_direction: str = "export") -> List[Dict]:
    """
    Extract invoice data from Excel file.
    Handles multiple formats including JD Sports, RS Components, etc.
    Also extracts metadata like entry type (B1/H1) and type (E/I).
    
    Args:
        file_obj: File-like object from Streamlit file_uploader
        trade_direction: "export" or "import"
        
    Returns:
        List of line items. First item may be metadata dict if found.
    """
    try:
        # Reset file pointer
        file_obj.seek(0)
        
        # Read Excel file - try all sheets
        excel_file = pd.ExcelFile(file_obj)
        all_items = []
        
        # Extract entry type (B1/H1) and type (E/I) from first few rows
        # These are typically in the header area before data
        excel_metadata = {}
        try:
            first_sheet = excel_file.sheet_names[0] if excel_file.sheet_names else None
            if first_sheet:
                file_obj.seek(0)
                df_header = pd.read_excel(file_obj, sheet_name=first_sheet, nrows=10, header=None)
                
                # Flatten the first 10 rows and search for entry type / type codes
                header_text = ' '.join(str(v) for v in df_header.values.flatten() if pd.notna(v))
                
                # Search for entry type (B1 or H1)
                if ' B1 ' in f' {header_text} ':
                    excel_metadata['entry_type'] = 'B1'
                    excel_metadata['direction_detected'] = 'export'
                elif ' H1 ' in f' {header_text} ':
                    excel_metadata['entry_type'] = 'H1'
                    excel_metadata['direction_detected'] = 'import'
                else:
                    # Try finding them without spaces
                    if 'B1' in header_text:
                        excel_metadata['entry_type'] = 'B1'
                    if 'H1' in header_text:
                        excel_metadata['entry_type'] = 'H1'
                
                # Search for type (E for export, I for import)
                if ' E ' in f' {header_text} ':
                    excel_metadata['type'] = 'E'
                elif ' I ' in f' {header_text} ':
                    excel_metadata['type'] = 'I'
                
                # If metadata found, store as first item with special marker
                if excel_metadata:
                    all_items.append({
                        '_excel_metadata': True,
                        **excel_metadata
                    })
        except Exception:
            pass  # Continue without metadata if extraction fails
        
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(file_obj, sheet_name=sheet_name, header=None)
            
            # Skip sheets with very few rows (likely summary/cover sheets)
            if len(df) < 2:
                continue
            
            # --- Auto-detect header row ---
            # Scan first 40 rows for one that looks like a column header
            # (contains keywords like HS CODE, DESCRIPTION, QTY, etc.)
            header_keywords = {'hs code', 'commodity code', 'tariff', 'description',
                               'qty', 'quantity', 'value', 'weight', 'origin',
                               'uom', 'unit cost', 'line total', 'total', 'hs'}
            header_row = None
            max_scan = min(40, len(df))
            
            for i in range(max_scan):
                row_vals = [str(v).lower().strip() for v in df.iloc[i] if pd.notna(v)]
                matches = sum(1 for v in row_vals if any(kw in v for kw in header_keywords))
                if matches >= 3:  # At least 3 recognised column keywords
                    header_row = i
                    break
            
            if header_row is not None:
                # Re-read with detected header row
                file_obj.seek(0)
                df = pd.read_excel(file_obj, sheet_name=sheet_name, header=header_row)
            else:
                # Fall back to first row as header (original behaviour)
                file_obj.seek(0)
                df = pd.read_excel(file_obj, sheet_name=sheet_name)
            
            # Drop completely empty rows
            df = df.dropna(how='all')
            
            # Normalise column names for matching
            col_lower = {col: str(col).lower().strip() for col in df.columns}
            
            # --- Skip reference/lookup sheets that have no value AND qty columns ---
            # Invoice sheets have LINE TOTAL/VALUE + QTY. Reference sheets typically lack one or both.
            _value_check = _find_columns(df.columns, col_lower, [
                ['line total', 'line_total', 'line value'],
                ['total value', 'total_value', 'invoice value'],
                ['value', 'price'],
                ['total', 'cost', 'amount'],
            ])
            _qty_check = _find_columns(df.columns, col_lower, [
                ['qty', 'quantity'],
            ])
            # Fallback: if value column not found by name, check unnamed columns for numeric data
            # (handles formats like Rhenus where value/price column has no header text)
            if not _value_check and _qty_check:
                for _col in df.columns:
                    _cl = col_lower.get(_col, str(_col).lower())
                    if not _cl.startswith('unnamed:'):
                        continue
                    _nums = pd.to_numeric(df[_col], errors='coerce').dropna()
                    if len(_nums) >= max(3, len(df) * 0.4) and _nums.mean() > 0.01:
                        _value_check = [_col]
                        break
            if not _value_check or not _qty_check:
                # Must have BOTH value and quantity columns to be an invoice sheet
                continue
            
            # --- Column detection with priority ordering ---
            # HS / Commodity code columns
            code_columns = _find_columns(df.columns, col_lower, [
                ['hs code', 'hs_code'],                              # exact first
                ['commodity code', 'commodity_code', 'comm code'],
                ['tariff'],
                ['hs', 'commodity', 'classification'],               # partial
            ])
            
            # Description columns
            desc_columns = _find_columns(df.columns, col_lower, [
                ['description', 'goods desc', 'goods description'],  # exact first
                ['desc', 'product', 'item name', 'item description'],
                ['item', 'name'],                                    # partial fallback
            ])
            
            # Quantity columns
            qty_columns = _find_columns(df.columns, col_lower, [
                ['qty', 'quantity'],
                ['count'],
            ])
            
            # Value columns — prefer "line total" over generic "total" or "cost"
            value_columns = _find_columns(df.columns, col_lower, [
                ['line total', 'line_total', 'line value'],          # exact line total
                ['total value', 'total_value', 'invoice value'],
                ['value', 'price'],
                ['total', 'cost', 'amount'],                         # less specific
            ])
            # Fallback: unnamed column with numeric data (e.g. Rhenus format)
            if not value_columns:
                _skip = set(c for cols in [code_columns, desc_columns, qty_columns] for c in cols)
                for _col in df.columns:
                    if _col in _skip:
                        continue
                    _cl = col_lower.get(_col, str(_col).lower())
                    if not _cl.startswith('unnamed:'):
                        continue
                    _nums = pd.to_numeric(df[_col], errors='coerce').dropna()
                    if len(_nums) >= max(3, len(df) * 0.4) and _nums.mean() > 0.01:
                        value_columns = [_col]
                        break
            
            # Weight columns — prefer "total (kg)" over "unit (kg)"
            weight_columns = _find_columns(df.columns, col_lower, [
                ['total (kg)', 'total_kg', 'total weight', 'net weight', 'nett weight'],
                ['net_weight', 'weight (kg)', 'weight_kg'],
                ['weight', 'net', 'gross', 'kg'],
            ])
            
            country_columns = _find_columns(df.columns, col_lower, [
                ['country of destination', 'country of origin', 'destination', 'origin country'],
                ['origin', 'country', 'coo', 'c.o.o'],
            ])
            # Fallback: unnamed column where values look like 2-3 letter country codes
            if not country_columns:
                _used = set(value_columns)
                for _col in df.columns:
                    if _col in _used:
                        continue
                    _cl = col_lower.get(_col, str(_col).lower())
                    if not _cl.startswith('unnamed:'):
                        continue
                    _vals = df[_col].dropna().astype(str).str.strip()
                    _cc_like = _vals.str.match(r'^[A-Z]{2,3}$').sum()
                    if _cc_like >= max(3, len(_vals) * 0.5):
                        country_columns = [_col]
                        break
            
            # UOM columns
            uom_columns = _find_columns(df.columns, col_lower, [
                ['uom', 'unit of measure'],
                ['unit'],
            ])
            
            # Material columns (for JD Sports - may contain useful info)
            material_columns = _find_columns(df.columns, col_lower, [
                ['material'],
            ])
            
            # Extract rows — stop at end-of-data boundary
            consecutive_empty = 0
            max_empty_gap = 3  # Stop after 3 consecutive rows with no HS code
            
            for idx, row in df.iterrows():
                # --- End-of-data detection ---
                # Check if this is a TOTAL / summary row (signals end of items)
                row_text = ' '.join(str(v).lower() for v in row if pd.notna(v))
                if any(marker in row_text for marker in ['total value', 'grand total', 'invoice total', 'sub total', 'subtotal']):
                    break
                
                # Try to find commodity code
                commodity_code = None
                if code_columns:
                    for col in code_columns:
                        val = row[col]
                        if pd.notna(val):
                            # Convert to string and clean
                            code_str = str(val).replace('.0', '').replace(' ', '').replace('-', '')
                            if code_str.isdigit() and len(code_str) >= 6:
                                commodity_code = code_str
                                break
                
                # If no code found, track consecutive empties
                if not commodity_code:
                    consecutive_empty += 1
                    if consecutive_empty >= max_empty_gap:
                        break  # End of data section
                    continue
                
                # Valid row found — reset gap counter
                consecutive_empty = 0
                
                # Truncate to 8 digits for export, pad to 10 for import
                if trade_direction.lower() == "export":
                    commodity_code = commodity_code[:8]
                elif trade_direction.lower() == "import" and len(commodity_code) == 8:
                    commodity_code = commodity_code + "00"
                
                # Extract other fields
                description = None
                if desc_columns:
                    for col in desc_columns:
                        if pd.notna(row[col]):
                            description = str(row[col]).strip()
                            break
                
                quantity = None
                if qty_columns:
                    for col in qty_columns:
                        if pd.notna(row[col]):
                            try:
                                quantity = float(_clean_numeric(row[col]))
                                break
                            except:
                                pass
                
                value = None
                if value_columns:
                    for col in value_columns:
                        if pd.notna(row[col]):
                            try:
                                value = float(_clean_numeric(row[col]))
                                break
                            except:
                                pass
                
                weight = None
                if weight_columns:
                    for col in weight_columns:
                        if pd.notna(row[col]):
                            try:
                                weight = float(_clean_numeric(row[col]))
                                break
                            except:
                                pass
                
                country = None
                if country_columns:
                    for col in country_columns:
                        if pd.notna(row[col]):
                            country = str(row[col]).strip()
                            break
                
                uom = None
                if uom_columns:
                    for col in uom_columns:
                        if pd.notna(row[col]):
                            uom = str(row[col]).strip()
                            break
                
                # Skip phantom rows: must have a value > 0
                if not value or value <= 0:
                    continue
                
                # Create item with normalised field names
                item = {
                    "commodity_code": commodity_code,
                    "description": description if description else f"Item {commodity_code}",
                    "quantity": quantity,
                    "uom": uom or "PCS",
                    "total_value": value,
                    "country_of_origin": country or "",
                    "net_weight": weight,
                    "currency": "GBP",
                    "needs_review": False,
                }
                
                all_items.append(item)
        
        return all_items
        
    except Exception as e:
        return [{"error": f"Error reading Excel: {str(e)}"}]


def _clean_numeric(val) -> str:
    """Strip currency symbols, commas, and whitespace from a value for float conversion."""
    s = str(val).strip()
    # Remove common currency symbols and thousands separators
    for ch in ['£', '$', '€', '¥', ',', '\u00a3', '\u20ac']:
        s = s.replace(ch, '')
    return s.strip()


def _find_columns(columns, col_lower_map, priority_groups):
    """
    Find matching columns using prioritised keyword groups.
    Returns the first group that matches any columns.
    Each group is a list of keywords — columns are matched if they
    contain any keyword (case-insensitive).
    """
    for keywords in priority_groups:
        matches = []
        for col in columns:
            cl = col_lower_map.get(col, str(col).lower())
            for kw in keywords:
                if kw in cl:
                    matches.append(col)
                    break
        if matches:
            return matches
    return []


def extract_from_word(file_obj, trade_direction: str = "export") -> List[Dict]:
    """
    Extract invoice data from Word document.
    
    Args:
        file_obj: File-like object from Streamlit file_uploader
        trade_direction: "export" or "import"
        
    Returns:
        List of line items
    """
    if not DOCX_AVAILABLE:
        return [{"error": "python-docx library not installed"}]
    
    try:
        # Reset file pointer
        file_obj.seek(0)
        
        # Read Word document
        doc = Document(file_obj)
        
        # Extract all text
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        
        # Also extract from tables
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join(cell.text for cell in row.cells)
                full_text.append(row_text)
        
        # Join all text and use the existing parser
        text = '\n'.join(full_text)
        
        # Use the PDF parser logic (it works on text)
        from pdf_extractor import parse_line_items
        items = parse_line_items(text, trade_direction=trade_direction)
        
        return items
        
    except Exception as e:
        return [{"error": f"Error reading Word document: {str(e)}"}]


def extract_from_file(file_obj, filename: str, trade_direction: str = "export") -> Tuple[str, List[Dict], Dict]:
    """
    Extract data from uploaded file based on file type.
    
    Args:
        file_obj: File-like object from Streamlit file_uploader
        filename: Name of the file
        trade_direction: "export" or "import"
        
    Returns:
        Tuple of (extracted_text, list_of_items, metadata_dict)
    """
    try:
        # Reset file pointer at start
        file_obj.seek(0)
        
        file_ext = filename.lower().split('.')[-1]
        
        # Default metadata based on trade direction
        if trade_direction.lower() == 'import':
            metadata = {
                'cpc_code': '4000',  # Free circulation
                'valuation_method': '1',  # Transaction value
                'incoterm': None,
                'currency': 'GBP',
                'total_invoice_value': None,
                'total_gross_weight': None,
                'total_net_weight': None,
                'number_of_packages': None,
                'package_type': None,
                'invoice_number': None,
                'invoice_date': None,
            }
        else:  # export
            metadata = {
                'cpc_code': '1000',  # Permanent export
                'incoterm': None,
                'currency': 'GBP',
                'total_invoice_value': None,
                'total_gross_weight': None,
                'total_net_weight': None,
                'number_of_packages': None,
                'package_type': None,
                'invoice_number': None,
                'invoice_date': None,
            }
        
        if file_ext == 'pdf':
            text = extract_text_from_pdf(file_obj)
            items = parse_line_items(text, trade_direction=trade_direction)
            metadata.update(extract_invoice_metadata(text))
            return text, items, metadata
        
        elif file_ext in ['xlsx', 'xls']:
            items = extract_from_excel(file_obj, trade_direction)
            # Create a text representation for debug view
            text = f"Excel file: {filename}\nExtracted {len(items)} items from spreadsheet"
            return text, items, metadata
        
        elif file_ext in ['docx', 'doc']:
            items = extract_from_word(file_obj, trade_direction)
            text = "Word document processed - see extracted items below"
            return text, items, metadata
        
        else:
            return f"Unsupported file type: {file_ext}", [], metadata
            
    except Exception as e:
        error_msg = f"Error processing {filename}: {str(e)}"
        return error_msg, [], metadata
