import re

def clean_ocr_text(text: str) -> str:
    """
    Cleans up OCR text for better AI extraction:
    - Removes excessive whitespace
    - Fixes common OCR errors (e.g., misread characters)
    - Attempts to align columns by normalizing spaces
    """
    # Replace multiple spaces/tabs with a single space
    text = re.sub(r'[ \t]{2,}', ' ', text)
    # Remove leading/trailing whitespace on each line
    text = '\n'.join(line.strip() for line in text.splitlines())
    # Remove empty lines
    text = '\n'.join(line for line in text.splitlines() if line)
    # Common OCR error corrections (add more as needed)
    text = text.replace('O', '0')  # Letter O to zero
    text = text.replace('l', '1')  # Lowercase L to one
    # Add more corrections as needed
    return text
