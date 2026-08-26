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
    nlp = spacy.load("en_core_web_trf")
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
    """en_core_web_trf ships no static word-vector table (doc.vector is empty for a trf
    pipeline), so this mean-pools the transformer's last hidden layer across tokens instead —
    a serviceable sentence embedding for cosine-similarity use (see app/nlp/domain_router.py)."""
    return doc._.trf_data.last_hidden_layer_state.dataXd.mean(axis=0)


def extract_entities(text: str) -> list[dict]:
    """Deterministic NER + gazetteer entity tags for a short span of text (typically one claim).
    Used to cross-check/augment the LLM-self-reported `entities` field on extracted claims —
    the decomposer prompt asks gpt-4o to reproduce spaCy-style entity labels from memory, which
    is less consistent than actually running the gazetteer-augmented NER pipeline that's already
    loaded for domain classification."""
    doc = get_nlp()(text)
    return [{"text": ent.text, "label": ent.label_} for ent in doc.ents]
