"""
pdf_utils.py — PDF to text extraction using pdfplumber.

For digital/text-based PDFs (Maersk, Swift Flow, most carrier docs),
pdfplumber extracts text directly without needing OCR.
If a page returns no text (scanned), a warning is attached.
"""

import pdfplumber
import io
from typing import Tuple


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, list[str]]:
    """
    Extract all text from a PDF given its raw bytes.

    Returns:
        text    : full concatenated text across all pages
        warnings: list of warning strings (e.g. scanned pages detected)
    """
    warnings = []
    pages_text = []

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    pages_text.append(page_text)
                else:
                    warnings.append(
                        f"Page {i}/{total_pages}: no text layer detected — "
                        "page may be scanned or image-only."
                    )
                    # attempt table extraction as fallback
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                row_text = "  |  ".join(
                                    cell or "" for cell in row if cell is not None
                                )
                                if row_text.strip():
                                    pages_text.append(row_text)
    except Exception as e:
        warnings.append(f"PDF extraction error: {str(e)}")
        return "", warnings

    full_text = "\n\n".join(pages_text)

    if not full_text.strip():
        warnings.append(
            "No text could be extracted from any page. "
            "This PDF may be fully scanned — consider adding OCR (pytesseract) support."
        )

    return full_text, warnings


def get_pdf_metadata(file_bytes: bytes) -> dict:
    """Return basic PDF metadata (page count, title if present)."""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            meta = pdf.metadata or {}
            return {
                "page_count": len(pdf.pages),
                "title": meta.get("Title", ""),
                "author": meta.get("Author", ""),
                "creator": meta.get("Creator", ""),
            }
    except Exception:
        return {"page_count": 0, "title": "", "author": "", "creator": ""}
