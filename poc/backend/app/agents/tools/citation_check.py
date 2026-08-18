from agno.tools import tool


def citation_fidelity(cited_span: str, claimed_content: str) -> bool:
    """Deterministic pre-check, no LLM call: does the cited evidence span actually contain what
    was claimed about it? A simple containment check is enough for the POC."""
    return claimed_content.lower() in cited_span.lower()


@tool(
    name="check_citation_fidelity",
    description="Check whether a cited evidence span actually contains the content claimed about it.",
)
def check_citation_fidelity_tool(cited_span: str, claimed_content: str) -> str:
    passed = citation_fidelity(cited_span, claimed_content)
    verdict = "PASS — the cited span does contain the claimed content" if passed else "FAIL — the cited span does NOT contain the claimed content"
    return f"Citation fidelity check: {verdict}"
