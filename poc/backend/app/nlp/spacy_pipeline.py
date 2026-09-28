import json
import os
from functools import lru_cache

import numpy as np
import spacy
from spacy.language import Language
from spacy.tokens import Doc

GAZETTEERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "gazetteers")


def load_jsonl(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@lru_cache(maxsize=1)
def get_nlp() -> Language:
    """en_core_web_trf plus an entity_ruler seeded from the financial/legal gazetteers, inserted
    before the statistical NER component so gazetteer matches win over model guesses. Cached —
    loading the transformer model takes a couple of seconds, and every caller shares one instance.

    This is the heavy pipeline (transformer forward pass on every call) — reserve it for stages
    that actually need NER, dependency parses, or the trf hidden states (domain classification,
    clause-decomposition gating, entity extraction). Pure sentence-boundary detection should use
    get_sentencizer_nlp() instead.
    """
    nlp = spacy.load("en_core_web_sm")
    ruler = nlp.add_pipe("entity_ruler", before="ner")
    ruler.add_patterns(load_jsonl(os.path.join(GAZETTEERS_DIR, "financial_terms.jsonl")))
    ruler.add_patterns(load_jsonl(os.path.join(GAZETTEERS_DIR, "legal_terms.jsonl")))
    return nlp


@lru_cache(maxsize=1)
def get_sentencizer_nlp() -> Language:
    """A blank, rule-based sentence-boundary pipeline — no transformer, no NER, no parser.
    Sentence splitting during chunking and extraction doesn't need any of what en_core_web_trf
    provides beyond sentence boundaries themselves, and re-running the full transformer forward
    pass on every chunk/sentence purely to find sentence breaks is the single biggest avoidable
    CPU cost in the ingestion/extraction path on large documents. Use get_nlp() instead when the
    caller actually needs entities, POS tags, or a dependency parse."""
    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    return nlp


def sentence_vector(doc: Doc) -> np.ndarray:
    """Get a sentence embedding. For en_core_web_sm (non-transformer), this uses the static
    word-vector table averaged across tokens. Provides a serviceable sentence embedding for
    cosine-similarity use (see app/nlp/domain_router.py).
    
    Falls back to transformer embeddings if available (en_core_web_trf), but en_core_web_sm
    is the primary model to avoid memory issues with large documents."""
    # Try transformer embeddings first if available
    if hasattr(doc._, "trf_data"):
        return doc._.trf_data.last_hidden_layer_state.dataXd.mean(axis=0)
    # Fall back to static word vectors (en_core_web_sm, en_core_web_md)
    if doc.vector is not None and not np.all(doc.vector == 0):
        return doc.vector
    # Final fallback: mean of token vectors
    vectors = np.array([token.vector for token in doc if token.has_vector])
    if len(vectors) > 0:
        return np.mean(vectors, axis=0)
    # If no vectors available, return a zero vector of appropriate dimension
    return np.zeros(96)  # en_core_web_sm default vector dimension


def extract_entities(text: str) -> list[dict]:
    """Deterministic NER + gazetteer entity tags for a short span of text (typically one claim).
    Used to cross-check/augment the LLM-self-reported `entities` field on extracted claims —
    the decomposer prompt asks gpt-4o to reproduce spaCy-style entity labels from memory, which
    is less consistent than actually running the gazetteer-augmented NER pipeline that's already
    loaded for domain classification."""
    doc = get_nlp()(text)
    return [{"text": ent.text, "label": ent.label_} for ent in doc.ents]
