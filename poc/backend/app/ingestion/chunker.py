from app.ingestion.sentence_level_chunker import chunk_sentences, split_sentences


def _table_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows)


def chunk_document(elements: list[dict], document_title: str, state: dict | None = None) -> list[dict]:
    """Turn parsed elements into chunks at paragraph/table boundaries — never fixed token windows.

    Headings don't become chunks of their own; they update the running section title used to
    build each subsequent chunk's context_capsule.

    `state` (optional) carries {"section_title", "offset"} across calls, so a document can be
    chunked in pieces while context_capsule and char offsets stay continuous across the whole
    document. Omit it to chunk a full element list in one call, as the ingestion pipeline does.
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

        element_start = state["offset"]
        state["offset"] = element_start + len(text) + 1
        section_title = state["section_title"]

        sentences = split_sentences(text)
        sentence_chunks = chunk_sentences(sentences)

        for sentence_chunk in sentence_chunks:
            
            chunk = {
                "element_index": element_index,
                "chunk_type": el_type,
                "chunk_text": sentence_chunk.text,
                "context_capsule": (
                    f"{document_title} > {section_title}"
                    if section_title and section_title != document_title
                    else document_title
                ),
                "page_number": element.get("page_number"),
                # Character offsets into the whole document's text, so chunks sort into document
                # order (chunk_sweep relies on this). Consecutive chunks of one long paragraph
                # overlap by CHUNK_OVERLAP_SENTENCES sentences, so their ranges overlap too.
                "char_start": element_start + sentence_chunk.char_start,
                "char_end": element_start + sentence_chunk.char_start + len(sentence_chunk.text),
                "embedding": [],
            }
            if "ocr_confidence" in element:
                chunk["ocr_confidence"] = element["ocr_confidence"]
            chunks.append(chunk)

    return chunks


def is_low_confidence_ocr(chunk: dict, config: dict) -> bool:
    confidence = chunk.get("ocr_confidence")
    return confidence is not None and confidence < config["ocr_confidence_threshold"]
