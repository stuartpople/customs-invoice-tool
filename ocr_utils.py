import re

def clean_ocr_text(text):
    """
    Light cleanup for OCR / extracted text before AI fallback.

    Must preserve newlines — vertical invoice layouts (IKF/ATI/etc.) put one
    field per line. Never apply destructive OCR letter substitutions globally
    (O→0 / l→1) — that corrupts tokens like CofO and Seller.
    """
    if not text:
        return text
    # Strip control chars except tab/newline/carriage-return
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
