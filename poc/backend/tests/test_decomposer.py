import pytest

from app.agents.decomposer import decompose_sentence
from app.llm.client import has_llm_credentials

HAS_API_KEY = has_llm_credentials()
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="no LLM credentials set for the active LLM_PROVIDER — LLM stage is BLOCKED-CREDENTIALS")

EXAMPLE_A_SENTENCE = (
    "Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost discipline, "
    "positioning us ahead of our closest competitor."
)
EXAMPLE_A_CONTEXT = "Q3 Business Report > Financial Highlights"

# the exact documented shape from the plan's canonical examples section — claim_type/scope only,
# since exact wording is model-dependent
EXAMPLE_A_EXPECTED = [
    {"claim_type": "statistical", "scope": "internal"},
    {"claim_type": "causal", "scope": "internal"},
    {"claim_type": "causal", "scope": "internal"},
    {"claim_type": "comparative", "scope": "both"},
]


@requires_llm
async def test_example_a_decomposes_to_documented_shape():
    result = await decompose_sentence(EXAMPLE_A_SENTENCE, EXAMPLE_A_CONTEXT)

    assert len(result.claims) == 4
    actual = [{"claim_type": c.claim_type, "scope": c.scope} for c in result.claims]
    assert actual == EXAMPLE_A_EXPECTED
