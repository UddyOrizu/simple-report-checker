"""One-off generator for the .pdf test fixtures, built with PyMuPDF + Pillow.
Run with `python build_pdf_fixtures.py`.
"""

import os

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

FIXTURES_DIR = os.path.dirname(__file__)

BODY_SIZE = 11
HEADING_SIZE = 16
MARGIN = 50
PAGE_W, PAGE_H = 612, 792  # US Letter


def _font(size: int, bold: bool = False):
    candidates = (
        ["/System/Library/Fonts/Helvetica.ttc"]
        if not bold
        else ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def add_heading(page, y, text, size=HEADING_SIZE):
    page.insert_text((MARGIN, y), text, fontsize=size, fontname="helv", color=(0, 0, 0))
    return y + size + 10


def add_paragraph(page, y, text, size=BODY_SIZE, max_width=PAGE_W - 2 * MARGIN):
    words = text.split()
    line = ""
    lines = []
    for w in words:
        trial = f"{line} {w}".strip()
        if fitz.get_text_length(trial, fontname="helv", fontsize=size) > max_width:
            lines.append(line)
            line = w
        else:
            line = trial
    if line:
        lines.append(line)
    for ln in lines:
        page.insert_text((MARGIN, y), ln, fontsize=size, fontname="helv", color=(0, 0, 0))
        y += size + 6
    return y + 8


def add_table(page, y, rows, col_widths=(260, 120)):
    row_h = 22
    x0 = MARGIN
    total_w = sum(col_widths)
    for r, row in enumerate(rows):
        ry = y + r * row_h
        page.draw_rect(fitz.Rect(x0, ry, x0 + total_w, ry + row_h), color=(0, 0, 0), width=0.75)
        cx = x0
        for c, cell in enumerate(row):
            page.draw_line((cx, ry), (cx, ry + row_h), color=(0, 0, 0), width=0.75)
            page.insert_text((cx + 6, ry + 15), str(cell), fontsize=BODY_SIZE, fontname="helv")
            cx += col_widths[c]
        page.draw_line((cx, ry), (cx, ry + row_h), color=(0, 0, 0), width=0.75)
    return y + len(rows) * row_h + 15


def build_native_report():
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    y = add_heading(page, y, "Q3 Business Report")
    y = add_heading(page, y, "Executive Summary", size=13)
    y = add_paragraph(
        page,
        y,
        "This report summarizes our performance for the third quarter, covering financial "
        "results, regional performance, and forward-looking guidance.",
    )
    y = add_heading(page, y, "Financial Highlights", size=13)
    y = add_paragraph(
        page,
        y,
        "Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost "
        "discipline, positioning us ahead of our closest competitor.",
    )
    y = add_table(page, y, [("Metric", "Value"), ("Revenue (current period)", "$112M"), ("Revenue (prior period)", "$100M")])
    y = add_heading(page, y, "Outlook", size=13)
    add_paragraph(page, y, "We expect continued momentum into the fourth quarter, subject to macroeconomic conditions.")

    doc.save(os.path.join(FIXTURES_DIR, "native_report.pdf"))
    doc.close()


def _render_scanned_page_image(lines: list[str], size=(1700, 2200)) -> Image.Image:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = _font(34)
    y = 100
    for line in lines:
        draw.text((100, y), line, fill="black", font=font)
        y += 60
    return img


def build_scanned_report():
    """Image-only pages, no text layer at all — requires OCR to read."""
    doc = fitz.open()
    pages_text = [
        [
            "Annual Compliance Memo",
            "",
            "All departments must complete mandatory GDPR training",
            "by the end of this quarter. Compliance is tracked",
            "centrally and reported to the board.",
        ],
        [
            "Section 2: Vendor Review",
            "",
            "Our top vendor by spend renewed its contract under the",
            "same indemnification terms as last year.",
        ],
    ]
    for lines in pages_text:
        img = _render_scanned_page_image(lines)
        img_path = os.path.join(FIXTURES_DIR, "_tmp_scan_page.png")
        img.save(img_path)
        rect = fitz.Rect(0, 0, PAGE_W, PAGE_H)
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_image(rect, filename=img_path)
        os.remove(img_path)
    doc.save(os.path.join(FIXTURES_DIR, "scanned_report.pdf"))
    doc.close()


def build_mixed_report():
    """Page 1 native text, page 2 scanned image-only, page 3 native text again."""
    doc = fitz.open()

    page1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    y = add_heading(page1, y, "Cover Page")
    add_paragraph(page1, y, "This document mixes natively generated pages with scanned image pages.")

    img = _render_scanned_page_image(
        ["Scanned Appendix A", "", "This page exists only as a scanned image", "and has no embedded text layer."]
    )
    img_path = os.path.join(FIXTURES_DIR, "_tmp_mixed_scan.png")
    img.save(img_path)
    page2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    page2.insert_image(fitz.Rect(0, 0, PAGE_W, PAGE_H), filename=img_path)
    os.remove(img_path)

    page3 = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    y = add_heading(page3, y, "Closing Notes")
    add_paragraph(page3, y, "Native text resumes on this final page of the mixed-format fixture.")

    doc.save(os.path.join(FIXTURES_DIR, "mixed_report.pdf"))
    doc.close()


def build_unstructured_essay():
    """Long document, zero headings, several clear topic shifts — exercises pseudo-sectioning."""
    doc = fitz.open()

    topic_paragraphs = [
        (
            "Remote work has reshaped how companies think about office space. Many organizations "
            "have downsized their physical footprint, renegotiating leases and converting former "
            "floors into shared collaboration space. Commercial real estate markets in major cities "
            "have felt this shift acutely, with vacancy rates climbing in several downtown cores."
        )
        * 1,
        (
            "Diet and exercise remain the two most reliable levers for long-term health outcomes. "
            "Clinical studies consistently show that moderate, regular physical activity reduces the "
            "risk of cardiovascular disease, while dietary patterns rich in vegetables and whole grains "
            "correlate with lower rates of metabolic syndrome across nearly every population studied."
        ),
        (
            "The history of transcontinental rail construction is a story of ambition, labor, and "
            "engineering under pressure. Crews worked through mountain ranges and harsh winters, laying "
            "track at a pace that would have seemed impossible a generation earlier, financed by a mix "
            "of government land grants and speculative private capital."
        ),
        (
            "Modern software deployment pipelines increasingly rely on automated testing gates before "
            "any change reaches production. Continuous integration systems run unit, integration, and "
            "end-to-end suites on every commit, and a failing gate blocks the merge, catching regressions "
            "long before they would otherwise reach real users."
        ),
        (
            "Coral reefs support an outsized share of marine biodiversity relative to the ocean floor "
            "area they occupy. Rising sea temperatures have driven repeated bleaching events over the "
            "past two decades, and recovery between events has grown shorter as thermal stress becomes "
            "more frequent across tropical waters worldwide."
        ),
        (
            "Central banks use interest rate policy as their primary tool for managing inflation. Raising "
            "rates tends to cool borrowing and spending, while cutting them stimulates investment, though "
            "the lag between a policy change and its visible economic effect can run anywhere from several "
            "months to over a year, complicating real-time decision-making."
        ),
    ]

    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    for para in topic_paragraphs:
        if y > PAGE_H - MARGIN - 80:
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN
        y = add_paragraph(page, y, para)
        y += 20  # extra gap between unrelated topics

    # pad to exceed the short-document page threshold (5 pages)
    while len(doc) <= 6:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        add_paragraph(page, MARGIN, topic_paragraphs[len(doc) % len(topic_paragraphs)])

    doc.save(os.path.join(FIXTURES_DIR, "unstructured_essay.pdf"))
    doc.close()


_FILLER_SENTENCES = [
    "Operational efficiency continued to improve as the team streamlined several internal processes.",
    "Customer satisfaction scores remained stable across all regions this period.",
    "The engineering team shipped several reliability improvements ahead of schedule.",
    "Marketing spend was reallocated toward higher-performing channels mid-quarter.",
    "Supply chain lead times normalized after last quarter's disruptions.",
    "Headcount growth was modest, concentrated primarily in customer-facing roles.",
    "A handful of long-standing vendor contracts were renegotiated on improved terms.",
    "Employee retention held steady, with attrition below the prior-year average.",
]


def _filler_paragraph(seed: int) -> str:
    return " ".join(_FILLER_SENTENCES[(seed + i) % len(_FILLER_SENTENCES)] for i in range(4))


def build_large_report():
    """80+ pages, mostly native text with a handful of scanned (image-only) pages mixed in, and
    several headed sections — big enough that a naive synchronous OCR/parse loop would visibly
    stall. Carries a claim on page 1 whose supporting table sits ~75 pages later in a different
    section, on its own; Phase 5.1.1's cross-reference fallback depends on that far-apart pair.
    """
    doc = fitz.open()
    scanned_pages = {15, 28, 41, 54, 67}

    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    y = add_heading(page, y, "Annual Regional Performance Report")
    add_paragraph(
        page,
        y,
        "Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost discipline, "
        "as detailed in the Regional Performance Appendix.",
    )

    section_names = [
        "Engineering Update", "Customer Success", "Marketing Overview", "Supply Chain Review",
        "People & Culture", "Vendor Relations", "Product Roadmap", "Risk & Compliance",
        "Infrastructure Notes", "Regional Operations",
    ]
    section_idx = 0

    while len(doc) < 77:
        current_page_number = len(doc) + 1

        if current_page_number in scanned_pages:
            # smaller than the other scanned fixtures' render size — this fixture needs 5 of
            # these embedded, and full-res pages would make it needlessly heavy for a test asset
            img = _render_scanned_page_image(
                [f"Field Notes -- Page {current_page_number}", "", "Scanned appendix page with no embedded text layer."],
                size=(850, 1100),
            )
            img_path = os.path.join(FIXTURES_DIR, "_tmp_large_scan.png")
            img.save(img_path)
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            page.insert_image(fitz.Rect(0, 0, PAGE_W, PAGE_H), filename=img_path)
            os.remove(img_path)
            continue

        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = MARGIN
        if current_page_number % 6 == 0:
            y = add_heading(page, y, section_names[section_idx % len(section_names)], size=13)
            section_idx += 1
        for i in range(3):
            y = add_paragraph(page, y, _filler_paragraph(current_page_number * 3 + i))
            if y > PAGE_H - MARGIN - 60:
                break

    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    y = add_heading(page, y, "Regional Performance Appendix")
    y = add_paragraph(page, y, "The table below breaks out revenue by period for the APAC region.")
    add_table(
        page,
        y,
        [("Metric", "Value"), ("Revenue (current period)", "$112M"), ("Revenue (prior period)", "$100M")],
    )

    while len(doc) < 80:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        add_paragraph(page, MARGIN, _filler_paragraph(len(doc)))

    doc.save(os.path.join(FIXTURES_DIR, "large_report.pdf"))
    doc.close()


if __name__ == "__main__":
    build_native_report()
    build_scanned_report()
    build_mixed_report()
    build_unstructured_essay()
    build_large_report()
    print("wrote native_report.pdf, scanned_report.pdf, mixed_report.pdf, unstructured_essay.pdf, large_report.pdf")
