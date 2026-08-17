You are decomposing a sentence from a business report into atomic, independently verifiable claims.

For each claim, output:
- text: the claim, self-contained (resolve pronouns/ellipsis using the provided context)
- claim_type: one of statistical, causal, comparative, definitional, forward_looking, hedged, opinion
- scope: internal (verifiable only from this document), external (needs sources outside this document),
  or both (needs one fact from inside the document and one from outside)
- source_span: the exact text in the original sentence this claim came from
- requires: a short list of what's needed to verify it

A claim is internal if everything needed to check it would reasonably appear in this same document
(a number, a defined term, a fact stated elsewhere). It's external if it needs something this document
has no reason to contain (competitor data, industry benchmarks, regulatory text, general world facts).
It's both if it's a comparison between something inside the document and something outside it.

Do not invent claims the sentence does not make. Do not skip a claim bundled into a longer sentence.

Context: {context_capsule}
Sentence: {sentence}
