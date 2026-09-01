You are choosing which section of a document most likely contains the evidence needed to verify
a claim, given only each section's title and a short navigational summary — the same way a human
would use a table of contents rather than reading the whole document.

What the claim needs: 
<Requires>
{requires}
</Requires>

Candidate sections (id, title, summary)
<Candidates>
{candidates}
</Candidates>

Pick the single section most likely to contain this evidence. If none of the candidates
plausibly contain it, say so explicitly rather than guessing — a wrong guess is worse than
admitting the document doesn't have it in an obvious place.

Return section_id (the chosen section's id, or null if genuinely none apply) and a short
reasoning for the choice.
