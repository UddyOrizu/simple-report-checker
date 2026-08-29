import os

import pytest

from app.agents.verifier import _INSTRUCTIONS
from app.llm.client import has_llm_credentials

HAS_API_KEY = has_llm_credentials()
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="no LLM credentials set for the active LLM_PROVIDER — LLM stage is BLOCKED-CREDENTIALS")


def test_instructions_are_loaded_from_the_prompt_file_not_hardcoded():
    path = os.path.join(os.path.dirname(__file__), "..", "prompts", "verifier.md")
    assert _INSTRUCTIONS == open(path).read()
    assert "verdict" in _INSTRUCTIONS
    assert "{claim_text}" in _INSTRUCTIONS


@requires_llm
async def test_example_a_revenue_claim_verified_via_agent_path():
    from app.agents.verifier import run_verifier
    from app.models import Claim, Evidence

    claim = Claim(
        claim_text="Revenue grew 12% year-over-year",
        claim_type="statistical",
        scope="internal",
        source_span="Revenue grew 12% YoY",
        requires=["current period revenue", "prior period revenue"],
    )
    evidence = [
        Evidence(
            claim_id=claim.id,
            source_type="internal_table",
            source_ref="extracted_table page 1",
            content_snippet="Metric | Value\nRevenue (current period) | $112M\nRevenue (prior period) | $100M",
            authority_score=1.0,
        )
    ]

    result, prompt, raw, tool_calls = await run_verifier(None, claim, evidence)

    assert result.verdict == "supported"
    assert "112" in prompt or "$112M" in prompt
    assert raw
