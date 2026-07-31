import re

def clean_ocr_text(text: str) -> str:
    """
    Light per-line OCR cleanup. Preserves newlines and does not globally
    remap letters (O→0 / l→1), which corrupts tokens like CofO.
    """
    if not text:
        return text
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    text = '\n'.join(line for line in text.splitlines() if line)
    return text
