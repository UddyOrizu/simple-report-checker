def _table_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)


def chunk_document(elements: list[dict], document_title: str, state: dict | None = None) -> list[dict]:
    """Turn parsed elements into chunks at paragraph/table boundaries — never fixed token windows.

    Headings don't become chunks of their own; they update the running section title used to
    build each subsequent chunk's context_capsule.

    `state` (optional) carries {"section_title", "offset"} across calls, so a large document can
    be chunked page-by-page (see app/ingestion/large_file.py) without holding every page's
    elements in memory at once, while context_capsule and char offsets stay continuous across the
    whole document. Omit it to chunk a full element list in one call, as every other caller does.
    """
    if state is None:
        state = {"section_title": None, "offset": 0}
    chunks = []

    for element_index, element in enumerate(elements):
        el_type = element["type"]
        text = _table_text(element["data"]) if el_type == "table" else element["text"]

        if el_type == "heading":
            state["section_title"] = element["text"]
            state["offset"] += len(text) + 1
            continue

        char_start = state["offset"]
        char_end = state["offset"] + len(text)
        state["offset"] = char_end + 1
        section_title = state["section_title"]

        chunk = {
            "element_index": element_index,
            "chunk_type": el_type,
            "chunk_text": text,
            "context_capsule": (
                f"{document_title} > {section_title}"
                if section_title and section_title != document_title
                else document_title
            ),
            "page_number": element.get("page_number"),
            "char_start": char_start,
            "char_end": char_end,
        }
        if "ocr_confidence" in element:
            chunk["ocr_confidence"] = element["ocr_confidence"]
        chunks.append(chunk)

    return chunks


def is_low_confidence_ocr(chunk: dict, config: dict) -> bool:
    confidence = chunk.get("ocr_confidence")
    return confidence is not None and confidence < config["ocr_confidence_threshold"]
