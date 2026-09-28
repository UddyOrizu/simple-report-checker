"""PDF -> Markdown via marker-pdf 0.3.2 (https://pypi.org/project/marker-pdf/0.3.2/).

marker is an optional dependency (`pip install -e ".[marker]"`): it pulls in torch + surya's
layout/OCR/table models (several GB, downloaded on first use) and pins older Pillow/regex than the
rest of the backend, so a deployment that doesn't want it keeps the pdfplumber/OCR parser instead —
see parse_document in app/ingestion/pipeline.py, which checks marker_available() first.
"""

import importlib.util
import threading

# marker's layout/OCR/texify/table models are expensive to load (tens of seconds, GBs of RAM) —
# loaded once per process on first use and reused for every later document, never at import time,
# so the API still starts instantly when no PDF is ever uploaded.
_models = None
_models_lock = threading.Lock()


def marker_available() -> bool:
    return importlib.util.find_spec("marker") is not None


def _load_models():
    global _models
    with _models_lock:
        if _models is None:
            from marker.models import load_all_models

            _models = load_all_models()
    return _models


def pdf_to_markdown(path: str, config: dict) -> str:
    """Converts a whole PDF to Markdown. Output is paginated — marker inserts its PAGE_SEPARATOR
    (a line of 48 dashes) at every page boundary — so markdown_parser can recover page numbers for
    citations. Image extraction is off: images aren't claims, and marker would otherwise emit
    `![...](...)` references to files we never save."""
    from marker.convert import convert_single_pdf
    from marker.settings import settings

    settings.PAGINATE_OUTPUT = True
    settings.EXTRACT_IMAGES = False

    conversion = config.get("markdown_conversion", {})
    full_text, _images, _metadata = convert_single_pdf(
        path,
        _load_models(),
        langs=conversion.get("marker_langs"),
        batch_multiplier=conversion.get("marker_batch_multiplier", 1),
        ocr_all_pages=conversion.get("marker_ocr_all_pages", False),
    )
    return full_text
