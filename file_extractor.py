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
    
    Args:
        file_obj: File-like object from Streamlit file_uploader
        trade_direction: "export" or "import"
        
    Returns:
        List of line items
    """
    try:
        # Reset file pointer
        file_obj.seek(0)
        
        # Read Excel file - try all sheets
        excel_file = pd.ExcelFile(file_obj)
        all_items = []
        
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(file_obj, sheet_name=sheet_name)
            
            # Look for columns that might contain commodity codes
            code_columns = [col for col in df.columns if any(
                keyword in str(col).lower() 
                for keyword in ['hs', 'commodity', 'tariff', 'code', 'classification']
            )]
            
            # Look for other relevant columns
            desc_columns = [col for col in df.columns if any(
                keyword in str(col).lower()
                for keyword in ['description', 'desc', 'item', 'product', 'name']
            )]
            
            qty_columns = [col for col in df.columns if any(
                keyword in str(col).lower()
                for keyword in ['qty', 'quantity', 'amount', 'count']
            )]
            
            value_columns = [col for col in df.columns if any(
                keyword in str(col).lower()
                for keyword in ['value', 'price', 'amount', 'total', 'cost']
            )]
            
            weight_columns = [col for col in df.columns if any(
                keyword in str(col).lower()
                for keyword in ['weight', 'net', 'gross', 'kg']
            )]
            
            country_columns = [col for col in df.columns if any(
                keyword in str(col).lower()
                for keyword in ['country', 'origin', 'coo', 'c.o.o']
            )]
            
            # Extract rows
            for idx, row in df.iterrows():
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
                                # Pad appropriately
                                if trade_direction.lower() == "import" and len(commodity_code) == 8:
                                    commodity_code = commodity_code + "00"
                                break
                
                # If no code found, skip this row
                if not commodity_code:
                    continue
                
                # Extract other fields
                description = None
                if desc_columns:
                    for col in desc_columns:
                        if pd.notna(row[col]):
                            description = str(row[col])
                            break
                
                quantity = None
                if qty_columns:
                    for col in qty_columns:
                        if pd.notna(row[col]):
                            try:
                                quantity = float(row[col])
                                break
                            except:
                                pass
                
                value = None
                if value_columns:
                    for col in value_columns:
                        if pd.notna(row[col]):
                            try:
                                value = float(row[col])
                                break
                            except:
                                pass
                
                weight = None
                if weight_columns:
                    for col in weight_columns:
                        if pd.notna(row[col]):
                            try:
                                weight = float(row[col])
                                break
                            except:
                                pass
                
                country = None
                if country_columns:
                    for col in country_columns:
                        if pd.notna(row[col]):
                            country = str(row[col]).strip()
                            break
                
                # Create item
                item = {
                    "commodity_code": commodity_code,
                    "description": description if description else f"Item {commodity_code}",
                    "quantity": quantity,
                    "unit": "units",
                    "value": value,
                    "country_origin": country,
                    "net_weight": weight
                }
                
                all_items.append(item)
        
        return all_items
        
    except Exception as e:
        return [{"error": f"Error reading Excel: {str(e)}"}]


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
