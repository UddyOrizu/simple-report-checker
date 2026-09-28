You are adding section headings to a document that has none, so that readers — and a system that
navigates the document by its table of contents — can find where each topic is discussed.

{window_note}

Below are the document's paragraphs in order, each prefixed with its index in [brackets]. Long
paragraphs are truncated with "…"; tables are marked "(table)".

<Paragraphs>
{paragraphs}
</Paragraphs>

Divide these paragraphs into sections wherever the topic genuinely changes, and give each section
a heading.

RULES:
1. start_index is the [index] of the paragraph the section begins with. Use only indices shown
   above, in increasing order.
2. Aim for sections of roughly {target_section_words} words. Don't make a section per paragraph,
   and don't lump unrelated topics together just to hit the size. A table normally belongs in
   the same section as the text that discusses it.
3. Titles must describe what the section actually covers, specifically enough to tell it apart
   from the others — "Q3 revenue by region", not "Financials" or "Discussion". Use the document's
   own terms (names, periods, metrics). 2-8 words, no numbering, no trailing punctuation.
4. level 1 = a major part of the document; level 2 = a subsection within the level-1 section
   before it. Use level 2 only when a major part clearly breaks into distinct sub-topics.
5. continues_previous: true if the first paragraph shown continues the topic from the previous
   part of the document (so it needs no new heading), false if it starts something new. Always
   false at the start of the document.
