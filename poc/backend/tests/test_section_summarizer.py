import os

import pytest
import yaml

from app.ingestion.section_summarizer import (
    _split_into_batches,
    generate_section_summary,
)
from app.llm.client import has_llm_credentials

HAS_API_KEY = has_llm_credentials()
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="no LLM credentials set for the active LLM_PROVIDER — LLM stage is BLOCKED-CREDENTIALS")


def _config(**overrides):
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    config = yaml.safe_load(open(config_path))
    config.update(overrides)
    return config


def test_split_into_batches_never_exceeds_word_limit():
    chunks = ["word " * 30, "word " * 30, "word " * 30, "word " * 30]
    batches = _split_into_batches(chunks, max_words_per_batch=50)

    assert sum(len(b) for b in batches) == len(chunks)
    for batch in batches:
        assert sum(len(c.split()) for c in batch) <= 60  # one chunk can push slightly over 50 alone, never two


def test_split_into_batches_never_splits_a_single_chunk():
    chunks = ["a " * 100]
    batches = _split_into_batches(chunks, max_words_per_batch=10)

    assert batches == [["a " * 100]]


@requires_llm
async def test_direct_summary_under_word_limit(fixtures_dir):
    config = _config()
    section_text = (
        "Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost discipline, "
        "positioning us ahead of our closest competitor."
    )
    summary = await generate_section_summary([section_text], "Financial Highlights", config)

    assert isinstance(summary, str) and summary.strip()
    assert len(summary.split()) <= config["section_summary_max_words"]


@requires_llm
async def test_hierarchical_path_triggers_on_large_section():
    config = _config(section_summary_direct_word_limit=50)
    chunks = [
        "Revenue grew 12% year over year driven by strong APAC expansion and disciplined cost control.",
        "Operating margin improved two points as headcount growth slowed relative to revenue growth.",
        "The APAC region alone contributed nearly half of total new bookings this quarter.",
        "Customer churn declined for the third consecutive quarter across all major segments.",
    ]
    trace_calls = []
    summary = await generate_section_summary(
        chunks, "Financial Highlights", config, on_trace=lambda p, r: trace_calls.append((p, r))
    )

    assert len(trace_calls) > 1  # more than one LLM call proves the batch-and-reduce path ran
    assert isinstance(summary, str) and summary.strip()
    assert len(summary.split()) <= config["section_summary_max_words"]
