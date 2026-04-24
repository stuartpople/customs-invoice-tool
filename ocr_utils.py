import re

def clean_ocr_text(text):
    """
    Cleans OCR text for better AI extraction:
    - Removes excessive whitespace
    - Fixes common OCR errors (e.g., misread characters)
    - Attempts to align columns by normalizing spaces
    - Removes non-printable/control characters
    """
    # Remove non-printable characters
    text = re.sub(r'[\x00-\x1F\x7F]', '', text)
    # Replace multiple spaces/tabs with a single space
    text = re.sub(r'[ \t]+', ' ', text)
    # Replace multiple newlines with a single newline
    text = re.sub(r'\n+', '\n', text)
    # Fix common OCR errors (add more as needed)
    text = text.replace('O', '0').replace('l', '1')  # Example: O→0, l→1
    # Remove leading/trailing whitespace
    text = text.strip()
    return text
