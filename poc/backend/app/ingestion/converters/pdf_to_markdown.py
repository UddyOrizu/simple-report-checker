"""PDF -> Markdown via PyMuPDF4LLM (https://pypi.org/project/PyMuPDF4LLM/).

PyMuPDF4LLM is an optional dependency (`pip install -e ".[pymupdf4llm]"`): it uses PyMuPDF's native
text extraction capabilities with improved layout understanding for structured Markdown output.
A deployment that doesn't want it keeps the pdfplumber/OCR parser instead — see
app/ingestion/conversion.py, which checks pymupdf4llm_available() first.
"""

import importlib.util
import threading

# PyMuPDF4LLM is loaded once per process on first use and reused for every later document,
# never at import time, so the API still starts instantly when no PDF is ever uploaded.
_pymupdf4llm = None
_pymupdf4llm_lock = threading.Lock()
# One conversion at a time per process to avoid concurrent PDF processing issues.
_convert_lock = threading.Lock()

# Separator to mark page boundaries in the output (consistent with marker's format)
PAGE_SEPARATOR = "\n\n" + "-" * 48 + "\n\n"


def pymupdf4llm_available() -> bool:
    return importlib.util.find_spec("pymupdf4llm") is not None


# Alias for backward compatibility
def marker_available() -> bool:
    """Backward compatibility: checks if PyMuPDF4LLM is available."""
    return pymupdf4llm_available()


def _load_pymupdf4llm():
    global _pymupdf4llm
    with _pymupdf4llm_lock:
        if _pymupdf4llm is None:
            import pymupdf4llm
            _pymupdf4llm = pymupdf4llm
    return _pymupdf4llm


def pdf_pages_to_markdown(path: str, start_page: int, max_pages: int, config: dict) -> str:
    """Converts pages [start_page, start_page + max_pages) (0-based) of a PDF to Markdown, so a
    large document can be converted in batches with progress in between. Output is paginated with
    PAGE_SEPARATOR (a line of 48 dashes) at page boundaries, so markdown_parser can recover page
    numbers.

    Image extraction is off by default: images aren't claims, and extracting them would add
    unnecessary data to the Markdown output."""
    pymupdf4llm = _load_pymupdf4llm()
    
    with _convert_lock:
        # Extract text from the specified page range
        markdown_parts = []
        for page_num in range(start_page, start_page + max_pages):
            page_markdown = pymupdf4llm.to_markdown(path, pages=[page_num])
            if page_markdown:
                markdown_parts.append(page_markdown)
                # Add page separator between pages
                if page_num < start_page + max_pages - 1:
                    markdown_parts.append(PAGE_SEPARATOR)
        
        return "".join(markdown_parts)
