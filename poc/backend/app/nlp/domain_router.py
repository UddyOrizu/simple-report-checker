import os
from functools import lru_cache

import numpy as np
import yaml

from app.nlp.spacy_pipeline import get_nlp, sentence_vector

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")
EXEMPLARS_PATH = os.path.join(CONFIG_DIR, "domain_exemplars.yaml")

GAZETTEER_LABEL_TO_DOMAIN = {"FINANCIAL_TERM": "financial", "LEGAL_TERM": "legal"}

# Keyword cues for inferring a section's domain_hint from its title alone (Phase 2.5's headings
# are short and generic — "Executive Summary" tells you nothing, "Legal Disclosures" does).
TITLE_DOMAIN_KEYWORDS = {
    "financial": ["financial", "revenue", "earnings"],
    "legal": ["legal", "compliance", "regulatory", "disclosures"],
}


def infer_domain_hint_from_title(title: str) -> str | None:
    lowered = title.lower()
    for domain, keywords in TITLE_DOMAIN_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return domain
    return None


def load_exemplars() -> dict[str, list[str]]:
    with open(EXEMPLARS_PATH) as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def _exemplar_vectors() -> dict[str, np.ndarray]:
    """Mean embedding vector per domain, from the seeded exemplar set in
    config/domain_exemplars.yaml — the semantic tier's reference points."""
    nlp = get_nlp()
    return {
        domain: np.mean([sentence_vector(nlp(s)) for s in sentences], axis=0)
        for domain, sentences in load_exemplars().items()
    }


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


def match_gazetteer(doc) -> str | None:
    """First gazetteer-tagged entity in the claim text resolves the domain."""
    for ent in doc.ents:
        if ent.label_ in GAZETTEER_LABEL_TO_DOMAIN:
            return GAZETTEER_LABEL_TO_DOMAIN[ent.label_]
    return None


def semantic_domain_classifier(doc) -> dict:
    """Cosine similarity against the seeded exemplar set — the fallback when neither the
    structural hint nor a gazetteer term resolves the domain."""
    scores = {domain: _cosine_similarity(sentence_vector(doc), vec) for domain, vec in _exemplar_vectors().items()}
    domain = max(scores, key=scores.get)
    return {"domain": domain, "score": max(0.0, scores[domain])}


def _confidence_ceiling(domain: str, registry: list[dict]) -> float | None:
    for row in registry:
        if row.get("domain") == domain and "confidence_ceiling" in row:
            return row["confidence_ceiling"]
    return None


def classify_domain(claim_text: str, section, registry: list[dict]) -> dict:
    """Three-tier cascade: a structural hint from the claim's section (Phase 2.5) beats a
    gazetteer terminology match, which beats the semantic-similarity fallback. `registry` is
    config/domain_registry.yaml's parsed rows — used here only to look up a domain's
    confidence_ceiling (e.g. the "general" catch-all never reports high confidence); it's the
    verification-method lookup itself that happens downstream, not in this function.
    """
    if section is not None and getattr(section, "domain_hint", None):
        result = {"domain": section.domain_hint, "confidence": 0.95, "source": "structural"}
    else:
        doc = get_nlp()(claim_text)
        domain = match_gazetteer(doc)
        if domain is not None:
            result = {"domain": domain, "confidence": 0.85, "source": "terminology"}
        else:
            semantic = semantic_domain_classifier(doc)
            result = {"domain": semantic["domain"], "confidence": semantic["score"], "source": "semantic"}

    ceiling = _confidence_ceiling(result["domain"], registry)
    if ceiling is not None:
        result["confidence"] = min(result["confidence"], ceiling)
    return result
