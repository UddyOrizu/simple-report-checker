"""PDF -> Markdown via marker-pdf 0.3.2 (https://pypi.org/project/marker-pdf/0.3.2/).

marker is an optional dependency (`pip install -e ".[marker]"`): it pulls in torch + surya's
layout/OCR/table models (several GB, downloaded on first use) and pins older Pillow/regex than the
rest of the backend, so a deployment that doesn't want it keeps the pdfplumber/OCR parser instead —
see app/ingestion/conversion.py, which checks marker_available() first.
"""

import importlib.util
import threading

# marker's layout/OCR/texify/table models are expensive to load (tens of seconds, GBs of RAM) —
# loaded once per process on first use and reused for every later document, never at import time,
# so the API still starts instantly when no PDF is ever uploaded.
_models = None
_models_lock = threading.Lock()
# One conversion at a time per process: every upload's background task shares the models above,
# and marker isn't built for concurrent inference on them (two PDFs at once can exhaust GPU memory
# or interleave state). Held per batch, not per document, so concurrent uploads take turns batch by
# batch and all keep reporting progress.
_convert_lock = threading.Lock()


def marker_available() -> bool:
    return importlib.util.find_spec("marker") is not None


def _load_models():
    global _models
    with _models_lock:
        if _models is None:
            from marker.models import load_all_models

            _models = load_all_models()
    return _models


def pdf_pages_to_markdown(path: str, start_page: int, max_pages: int, config: dict) -> str:
    """Converts pages [start_page, start_page + max_pages) (0-based) of a PDF to Markdown, so a
    large document can be converted in batches with progress in between. Output is paginated —
    marker inserts its PAGE_SEPARATOR (a line of 48 dashes) at page boundaries — so
    markdown_parser can recover page numbers. Caveat: marker drops blocks with no text, including
    a blank page's separator, so a batch containing an empty page comes back with fewer
    separators than pages; app/ingestion/conversion.py detects that and re-derives page numbers.

    Image extraction is off: images aren't claims, and marker would otherwise emit `![...](...)`
    references to files we never save."""
    from marker.convert import convert_single_pdf
    from marker.settings import settings

    settings.PAGINATE_OUTPUT = True
    settings.EXTRACT_IMAGES = False

    conversion = config.get("markdown_conversion", {})
    models = _load_models()
    with _convert_lock:
        full_text, _images, _metadata = convert_single_pdf(
            path,
            models,
            start_page=start_page,
            max_pages=max_pages,
            langs=conversion.get("marker_langs"),
            batch_multiplier=conversion.get("marker_batch_multiplier", 1),
            ocr_all_pages=conversion.get("marker_ocr_all_pages", False),
        )
    return full_text
