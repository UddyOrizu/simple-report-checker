"""Word (.docx) -> Markdown via mammoth (docx -> semantic HTML) + markdownify (HTML -> Markdown).

mammoth maps Word's paragraph styles to real HTML structure (Heading N -> <hN>, list styles ->
<ul>/<ol>, tables -> <table>), which markdownify then renders as ATX headings, list items and pipe
tables — the same shape marker produces for PDFs, so both formats share one markdown_parser.
"""

import mammoth
from markdownify import markdownify

# mammoth's defaults already cover "Heading 1".."Heading 6"; Word's "Title" style isn't mapped by
# default and would otherwise come through as a plain paragraph, losing the document's title.
_STYLE_MAP = """
p[style-name='Title'] => h1:fresh
p[style-name='Subtitle'] => h2:fresh
"""


def docx_to_markdown(path: str) -> str:
    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f, style_map=_STYLE_MAP)
    # strip=["img"]: embedded images come through as base64 data URIs — megabytes of noise in the
    # Markdown with no text to check.
    return markdownify(result.value, heading_style="ATX", bullets="-", strip=["img"])
