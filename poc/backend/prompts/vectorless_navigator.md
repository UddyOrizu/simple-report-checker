You are navigating a document's table of contents to find where the evidence for a claim lives.
You see only section titles and short summaries — never the full text — the same way a human
reader would scan a table of contents before turning to the right page.

The claim being checked:
<Claim>
{claim_text}
</Claim>

What is needed to verify it:
<Requires>
{requires}
</Requires>

Table of contents (indentation shows nesting; each line is [id] title: summary):
<Outline>
{outline}
</Outline>

Pick up to {max_sections} section ids most likely to contain evidence that confirms OR
contradicts the claim — the figures, statements, tables or definitions a reviewer would check it
against. Prefer the most specific (deepest) section that fits; picking a parent section reads all
of its subsections too, so only pick a parent when the evidence could be spread across them.

Do not pick the section the claim itself comes from unless it is the only plausible home for
supporting detail (e.g. a table beneath the claim). If no section plausibly holds the evidence,
return an empty list — a wrong guess is worse than admitting the document doesn't have it.

Return section_ids (ids exactly as shown in the outline, e.g. "2.1") and a short reasoning.
