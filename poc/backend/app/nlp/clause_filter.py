from app.nlp.spacy_pipeline import get_nlp

# dep labels that mark a token as heading its own clause (a distinct predicate), rather than
# just modifying another clause's verb
CLAUSE_VERB_DEPS = {"ROOT", "conj", "advcl", "ccomp", "relcl", "acl"}


def needs_decomposition(sentence: str) -> bool:
    """True if the sentence looks like it bundles multiple independently-checkable facts — a
    coordinating conjunction, or more than one clause-level predicate — and should go through the
    decomposition agent rather than becoming a single direct claim record."""
    doc = get_nlp()(sentence)
    has_coordinating_conjunction = any(token.dep_ == "cc" for token in doc)
    clause_verb_count = sum(1 for token in doc if token.pos_ in ("VERB", "AUX") and token.dep_ in CLAUSE_VERB_DEPS)
    return has_coordinating_conjunction or clause_verb_count > 1
