from app.nlp.spacy_pipeline import get_nlp


def test_gazetteer_matches_financial_term():
    nlp = get_nlp()
    doc = nlp("Our EBITDA improved significantly this quarter.")

    matches = [ent.text for ent in doc.ents if ent.label_ == "FINANCIAL_TERM"]
    assert "EBITDA" in matches


def test_gazetteer_matches_legal_term():
    nlp = get_nlp()
    doc = nlp("We remain committed to GDPR compliance across all regions.")

    matches = [ent.text for ent in doc.ents if ent.label_ == "LEGAL_TERM"]
    assert "GDPR" in matches


def test_get_nlp_is_cached():
    assert get_nlp() is get_nlp()
