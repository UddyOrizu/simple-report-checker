from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_block_items(doc: Document):
    """Walk a document's body, yielding Paragraph/Table objects in document order."""
    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def is_heading(block) -> bool:
    if not isinstance(block, Paragraph) or block.style is None:
        return False
    name = block.style.name
    return name == "Title" or name.startswith("Heading")


def heading_level(block: Paragraph) -> int:
    name = block.style.name  # "Title" (level 0) or "Heading N"
    if name == "Title":
        return 0
    digits = "".join(c for c in name if c.isdigit())
    return int(digits) if digits else 1


def is_table(block) -> bool:
    return isinstance(block, Table)


def table_to_rows(block: Table) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in block.rows]


def parse_docx(path: str) -> list[dict]:
    doc = Document(path)
    elements = []
    for block in iter_block_items(doc):
        if is_heading(block):
            elements.append({"type": "heading", "level": heading_level(block), "text": block.text})
        elif is_table(block):
            elements.append({"type": "table", "data": table_to_rows(block)})
        elif isinstance(block, Paragraph):
            if block.text.strip():
                elements.append({"type": "paragraph", "text": block.text})
    return elements
