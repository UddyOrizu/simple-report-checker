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
    """
    nlp = spacy.load("en_core_web_trf")
    ruler = nlp.add_pipe("entity_ruler", before="ner")
    ruler.add_patterns(load_jsonl(os.path.join(GAZETTEERS_DIR, "financial_terms.jsonl")))
    ruler.add_patterns(load_jsonl(os.path.join(GAZETTEERS_DIR, "legal_terms.jsonl")))
    return nlp


def sentence_vector(doc: Doc) -> np.ndarray:
    """en_core_web_trf ships no static word-vector table (doc.vector is empty for a trf
    pipeline), so this mean-pools the transformer's last hidden layer across tokens instead —
    a serviceable sentence embedding for cosine-similarity use (see app/nlp/domain_router.py)."""
    return doc._.trf_data.last_hidden_layer_state.dataXd.mean(axis=0)
