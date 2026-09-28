You are extracting evidence from one section of a document to check a claim made elsewhere in
the same document.

The claim being checked:
<Claim>
{claim_text}
</Claim>

The claim's own source sentence (never quote this — a claim cannot be its own evidence):
<Claim_Source>
{claim_source}
</Claim_Source>

Section: {section_path}
<Section_Text>
{section_text}
</Section_Text>

Find up to {max_quotes} passages in the section text that bear on whether the claim is true.

RULES:
1. Each quote must be copied VERBATIM from the section text — exact words, numbers and
   punctuation, no paraphrasing, no ellipses joining separate passages. Quotes are checked
   against the source text and any quote that doesn't appear there is discarded.
2. Keep each quote to the shortest span that carries the evidence (one to three sentences, or
   one table row including its column header if needed to make the figure meaningful).
3. stance: "supports" if the passage confirms the claim, "contradicts" if it conflicts with it
   (a different figure, date, or statement), "context" if it is relevant but neither confirms
   nor refutes it on its own (e.g. one input to a calculation the claim depends on).
4. explanation: one sentence on how the passage bears on the claim, citing the specific figure or
   statement.
5. If nothing in the section bears on the claim, return an empty list. Do not pad the list with
   loosely related passages.
