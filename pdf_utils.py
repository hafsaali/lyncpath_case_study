"""
pdf_utils.py — PDF to text extraction using pdfplumber.
"""

import pdfplumber
import io
from typing import Tuple


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, list]:
    warnings   = []
    pages_text = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text and text.strip():
                    pages_text.append(text)
                else:
                    warnings.append(f"Page {i}/{total}: no text layer detected.")
                    for table in page.extract_tables():
                        for row in table:
                            row_text = "  |  ".join(c or "" for c in row if c)
                            if row_text.strip():
                                pages_text.append(row_text)
    except Exception as e:
        warnings.append(f"PDF extraction error: {e}")
        return "", warnings

    full_text = "\n\n".join(pages_text)
    if not full_text.strip():
        warnings.append("No text extracted — PDF may be fully scanned.")
    return full_text, warnings


def get_pdf_metadata(file_bytes: bytes) -> dict:
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            meta = pdf.metadata or {}
            return {
                "page_count": len(pdf.pages),
                "title":   meta.get("Title", ""),
                "creator": meta.get("Creator", ""),
            }
    except Exception:
        return {"page_count": 0, "title": "", "creator": ""}