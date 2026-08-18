import os

import pytest

from app.agents.challenger import _INSTRUCTIONS

HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set — LLM stage is BLOCKED-CREDENTIALS")


def test_instructions_are_loaded_from_the_prompt_file_not_hardcoded():
    path = os.path.join(os.path.dirname(__file__), "..", "prompts", "challenger.md")
    assert _INSTRUCTIONS == open(path).read()
    assert "Citation fidelity" in _INSTRUCTIONS
    assert "{verifier_verdict}" in _INSTRUCTIONS


@requires_llm
async def test_challenger_rejects_when_citation_is_fabricated():
    from app.agents.challenger import run_challenger
    from app.models import Claim, Evidence
    from app.schemas.verification import VerifierResult

    claim = Claim(
        claim_text="Revenue grew 40% year-over-year",
        claim_type="statistical",
        scope="internal",
        source_span="Revenue grew 40% YoY",
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
    fabricated_verifier_result = VerifierResult(
        verdict="supported", confidence=0.9, reasoning="Revenue grew from $100M to $112M, a 40% increase.",
        citations=["extracted_table page 1"],
    )

    result, prompt, raw, tool_calls = await run_challenger(None, claim, evidence, fabricated_verifier_result)

    assert result.accepted_verdict != "supported"  # the 40% claim doesn't hold against 112 vs 100
