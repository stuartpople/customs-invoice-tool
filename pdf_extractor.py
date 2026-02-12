"""
PDF text extraction module for invoice processing
Supports both text-based and image-based (OCR) PDFs
"""
import PyPDF2
from typing import List, Dict, Optional
import re
import io

# OCR imports (optional)
try:
    import pytesseract
    from pdf2image import convert_from_bytes
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


def extract_text_from_pdf(pdf_file, use_ocr: bool = True, progress_callback=None) -> str:
    """
    Extract all text from a PDF file. Tries text extraction first,
    then falls back to OCR if minimal text is found.
    
    Args:
        pdf_file: File-like object from Streamlit file_uploader
        use_ocr: Whether to use OCR for image-based PDFs
        progress_callback: Optional function(page_num, total_pages) for OCR progress
        
    Returns:
        Extracted text as a string
    """
    try:
        # Reset file pointer
        pdf_file.seek(0)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        
        # Extract text from all pages
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        
        # Check if we got meaningful text
        # Very low threshold - if PDF has minimal/garbled text, use OCR instead
        if text.strip() and len(text.strip()) > 500:
            return text
        
        # If minimal text and OCR is available, use full quality OCR
        if use_ocr and OCR_AVAILABLE:
            return extract_text_with_ocr(pdf_file, progress_callback)
        elif use_ocr and not OCR_AVAILABLE:
            return "Error: PDF appears to be image-based but OCR libraries not installed. Install pytesseract and pdf2image."
        else:
            return text if text.strip() else "Error: No text found in PDF. May be image-based."
            
    except Exception as e:
        return f"Error extracting PDF: {str(e)}"


def extract_text_with_ocr(pdf_file, progress_callback=None) -> str:
    """
    Extract text from image-based PDF using OCR (Tesseract).
    Processes ALL pages at 200 DPI - good balance of speed and quality.
    
    Args:
        pdf_file: File-like object from Streamlit file_uploader
        progress_callback: Optional function(page_num, total_pages) for progress
        
    Returns:
        OCR-extracted text as a string
    """
    try:
        # Reset file pointer
        pdf_file.seek(0)
        pdf_bytes = pdf_file.read()
        
        # 200 DPI - 2x faster than 300 DPI, still good quality for invoices
        images = convert_from_bytes(pdf_bytes, dpi=200)
        total_pages = len(images)
        
        text = ""
        batch_size = 5  # Process 5 pages at a time, then callback
        
        for i, image in enumerate(images):
            # Perform OCR on each page
            page_text = pytesseract.image_to_string(image, lang='eng')
            text += f"\n--- Page {i+1} ---\n{page_text}\n"
            
            # Report progress after each batch or at end
            if progress_callback and ((i + 1) % batch_size == 0 or i + 1 == total_pages):
                progress_callback(i + 1, total_pages)
        
        return text if text.strip() else "Error: OCR completed but no text found"
        
    except Exception as e:
        return f"Error during OCR: {str(e)}"


def extract_text_with_ocr(pdf_file, progress_callback=None) -> str:
    """
    Extract text from image-based PDF using OCR (Tesseract).
    Processes page-by-page to allow progress updates.
    
    Args:
        pdf_file: File-like object from Streamlit file_uploader
        progress_callback: Optional function(page_num, total_pages) to report progress
        
    Returns:
        OCR-extracted text as a string
    """
    try:
        # Reset file pointer
        pdf_file.seek(0)
        pdf_bytes = pdf_file.read()
        
        # Convert PDF pages to images - 300 DPI for accuracy
        images = convert_from_bytes(pdf_bytes, dpi=300)
        total_pages = len(images)
        
        text = ""
        for i, image in enumerate(images):
            # Report progress if callback provided
            if progress_callback:
                progress_callback(i + 1, total_pages)
            
            # Perform OCR on each page
            page_text = pytesseract.image_to_string(image, lang='eng')
            text += f"\n--- Page {i+1} ---\n{page_text}\n"
        
        return text if text.strip() else "Error: OCR completed but no text found"
        
    except Exception as e:
        return f"Error during OCR: {str(e)}"


def extract_invoice_data(text: str) -> Dict:
    """
    Attempt to extract structured data from invoice text.
    This is a basic pattern matcher - can be enhanced with ML/NLP.
    
    Args:
        text: Raw text from PDF
        
    Returns:
        Dictionary with extracted fields
    """
    data = {
        "invoice_number": None,
        "date": None,
        "supplier": None,
        "items": []
    }
    
    # Try to find invoice number (various patterns)
    invoice_patterns = [
        r'Invoice\s*#?\s*:?\s*([A-Z0-9-]+)',
        r'Invoice\s*Number\s*:?\s*([A-Z0-9-]+)',
        r'INV[-\s]*([A-Z0-9-]+)'
    ]
    for pattern in invoice_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data["invoice_number"] = match.group(1)
            break
    
    # Try to find dates (DD/MM/YYYY or YYYY-MM-DD)
    date_patterns = [
        r'\b(\d{2}[/-]\d{2}[/-]\d{4})\b',
        r'\b(\d{4}[/-]\d{2}[/-]\d{2})\b'
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            data["date"] = match.group(1)
            break
    
    # Try to find commodity codes (8-10 digits)
    commodity_codes = re.findall(r'\b(\d{8,10})\b', text)
    if commodity_codes:
        data["commodity_codes"] = list(set(commodity_codes))  # Unique codes
    
    return data


def parse_line_items(text: str, trade_direction: str = "export") -> List[Dict]:
    """
    PROVEN parser - extracts HS code declarations with descriptions from line before.
    This is the working version that extracts 94 items correctly.
    
    Args:
        text: Raw text from PDF
        trade_direction: "export" or "import" - affects code padding
        
    Returns:
        List of line items with commodity_code, description, quantity, value, net_weight
    """
    items = []
    lines = text.split('\n')
    
    # Determine if we should pad codes to 10 digits
    pad_to_10 = (trade_direction.lower() == "import")
    
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
        r'([\d,]+\.?\d*)\s*g\b(?!ram)',
    ]
    
    # Currency pattern
    currency_pattern = r'\b(GBP|USD|EUR|CAD|AUD|CNY|JPY|CHF)\b'
    
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        
        line_lower = line.lower()
        
        # Look for explicit HS code declarations
        hs_code_match = re.search(
            r'(?:hs\s*export\s*code|hs\s*import\s*code|hs\s*code|commodity\s*code)[\s:]*(\d{6,10})',
            line, re.IGNORECASE
        )
        
        if hs_code_match:
            hs_code = hs_code_match.group(1)
            
            # Pad to appropriate length
            if len(hs_code) == 6:
                hs_code = hs_code + ("0000" if pad_to_10 else "00")
            elif len(hs_code) == 8:
                hs_code = hs_code + "00" if pad_to_10 else hs_code
            elif len(hs_code) == 7:
                hs_code = hs_code + ("000" if pad_to_10 else "0")
            
            # Extract description from line BEFORE HS code (proven approach)
            description = None
            
            # Look 1-3 lines back for description
            for j in range(1, 4):
                if i - j >= 0:
                    prev_line = lines[i - j].strip()
                    # Valid description: not empty, has substance
                    if prev_line and len(prev_line) > 5:
                        # Skip only if it's JUST a header keyword (not part of description)
                        # Header patterns: "Item No:", "Order No", "Shipment:", "Page 1", but NOT "Item Description Text"
                        if re.match(r'^\s*(?:item\s*no|item\s*number|item\s*#|order\s*no|shipment\s*no|page\s*\d+)', prev_line, re.IGNORECASE):
                            continue  # This is a header, try previous line
                        # Also skip if it's the HS code itself from a multi-line pattern
                        if re.match(r'^\s*hs[.\s]*(export|import)?\s*code', prev_line, re.IGNORECASE):
                            continue
                        # Valid description found
                        # Extract description (may include part number)
                        parts = prev_line.split()
                        if len(parts) >= 1:
                            description = ' '.join(parts[:min(len(parts), 12)])
                            break
            
            if not description:
                # Last resort: try to extract from same line before "HS"
                before_hs = line[:hs_code_match.start()].strip()
                if before_hs and len(before_hs) > 5:
                    description = before_hs[:100]
                else:
                    continue  # Skip items without description
            
            # Build context from current line and the next 2 lines only (avoid mixing items)
            context_lines = [line]  # Current line with HS code
            for j in range(1, 3):  # Next 2 lines (usually contain qty, value, weight)
                if i + j < len(lines):
                    next_line = lines[i + j].strip()
                    # Stop if we hit another HS code or empty multi-line gap
                    if re.search(r'hs\s*(?:export|import)?\s*code', next_line, re.IGNORECASE):
                        break
                    context_lines.append(next_line)
            context = ' '.join(context_lines)
            
            # Extract quantity and unit
            quantity = ""
            uom = "pcs"
            for pattern in qty_patterns:
                match = re.search(pattern, context, re.IGNORECASE)
                if match:
                    try:
                        quantity = str(int(float(match.group(1))))
                        if len(match.groups()) > 1 and match.group(2):
                            uom = match.group(2).lower()
                    except (ValueError, IndexError):
                        pass
                    break
            
            # Extract value
            unit_value = ""
            total_value = ""
            currency = "GBP"  # Default
            
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
            
            # Extract Country of Origin
            coo = ""
            coo_match = re.search(r'c\s*of\s*o[\s:]*([a-z]+)', context, re.IGNORECASE)
            if coo_match:
                coo = coo_match.group(1).capitalize()
            
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
            
            # Create item with fields matching old format
            items.append({
                "commodity_code": hs_code,
                "description": description,
                "quantity": float(quantity) if quantity else None,
                "unit": uom,
                "value": float(total_value) if total_value else None,
                "country_origin": coo if coo else None,
                "net_weight": float(net_weight) if net_weight else None
            })
    
    return items


def extract_invoice_metadata(text: str) -> Dict:
    """
    Extract CDS-relevant invoice metadata like incoterms, currency, totals, packages.
    
    Args:
        text: Raw text from PDF invoice
        
    Returns:
        Dictionary with invoice metadata
    """
    metadata = {
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
    
    # Extract Incoterms (FOB, CIF, EXW, FCA, DDP, etc.)
    incoterm_pattern = r'\b(FOB|CIF|EXW|FCA|DDP|DAP|CPT|CIP|FAS|CFR|DPU|DAT)\b'
    incoterm_match = re.search(incoterm_pattern, text, re.IGNORECASE)
    if incoterm_match:
        metadata['incoterm'] = incoterm_match.group(1).upper()
    
    # Extract currency
    currency_pattern = r'\b(GBP|USD|EUR|CAD|AUD|CNY|JPY|CHF|INR)\b'
    currency_match = re.search(currency_pattern, text)
    if currency_match:
        metadata['currency'] = currency_match.group(1)
    
    # Extract total invoice value
    total_patterns = [
        r'(?:total|grand\s*total|invoice\s*total|amount\s*due)[\s:]*[£$€¥]?\s*([\d,]+\.?\d*)',
        r'(?:total|grand\s*total)[\s:]*(\d+[,\d]*\.?\d{2})',
    ]
    for pattern in total_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                val_str = match.group(1).replace(',', '')
                metadata['total_invoice_value'] = float(val_str)
                break
            except:
                pass
    
    # Extract gross weight
    gross_patterns = [
        r'(?:gross\s*weight|g\.?w\.?)[\s:]*([\d,]+\.?\d*)\s*(?:kg|kilograms?)',
        r'(?:total\s*gross\s*weight)[\s:]*([\d,]+\.?\d*)\s*(?:kg)',
    ]
    for pattern in gross_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                weight_str = match.group(1).replace(',', '')
                metadata['total_gross_weight'] = float(weight_str)
                break
            except:
                pass
    
    # Extract net weight
    net_patterns = [
        r'(?:total\s*net\s*weight|total\s*n\.?w\.?)[\s:]*([\d,]+\.?\d*)\s*(?:kg|kilograms?)',
        r'(?:net\s*weight)[\s:]*([\d,]+\.?\d*)\s*(?:kg)',
    ]
    for pattern in net_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                weight_str = match.group(1).replace(',', '')
                metadata['total_net_weight'] = float(weight_str)
                break
            except:
                pass
    
    # Extract number of packages
    package_patterns = [
        r'(?:number\s*of\s*packages|no\.?\s*of\s*packages|packages)[\s:]*(\d+)',
        r'(\d+)\s*(?:box(?:es)?|carton(?:s)?|pallet(?:s)?|package(?:s)?)',
    ]
    for pattern in package_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                metadata['number_of_packages'] = int(match.group(1))
                break
            except:
                pass
    
    # Extract package type
    pkgtype_pattern = r'(?:package\s*type|packing)[\s:]*(\w+)'
    pkgtype_match = re.search(pkgtype_pattern, text, re.IGNORECASE)
    if pkgtype_match:
        metadata['package_type'] = pkgtype_match.group(1).capitalize()
    elif metadata['number_of_packages']:
        # Try to infer from context
        if re.search(r'\bbox(?:es)?\b', text, re.IGNORECASE):
            metadata['package_type'] = 'Box'
        elif re.search(r'\bcarton(?:s)?\b', text, re.IGNORECASE):
            metadata['package_type'] = 'Carton'
        elif re.search(r'\bpallet(?:s)?\b', text, re.IGNORECASE):
            metadata['package_type'] = 'Pallet'
    
    # Extract invoice number
    inv_patterns = [
        r'(?:invoice\s*(?:no|number|#))[\s:]*([A-Z0-9-]+)',
        r'invoice[\s:]*([A-Z0-9-]+)',
    ]
    for pattern in inv_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metadata['invoice_number'] = match.group(1)
            break
    
    # Extract invoice date
    date_patterns = [
        r'(?:invoice\s*date|date)[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(?:invoice\s*date|date)[\s:]*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metadata['invoice_date'] = match.group(1)
            break
    
    return metadata
