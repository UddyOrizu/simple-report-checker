import pytest

from app.agents.reconcile import derive_severity, reconcile
from app.schemas.verification import ChallengerCheck, ChallengerResult, VerifierResult

_OK_CHECK = ChallengerCheck(ok=True, reasoning="x")


def _verifier(verdict: str, confidence: float = 0.9) -> VerifierResult:
    return VerifierResult(verdict=verdict, confidence=confidence, reasoning="x", citations=["x"])


def _challenger(verdict: str, accepted_verdict: str, confidence: float = 0.8) -> ChallengerResult:
    return ChallengerResult(
        verdict=verdict,
        accepted_verdict=accepted_verdict,
        confidence=confidence,
        citation_fidelity=_OK_CHECK,
        basis_match=_OK_CHECK,
        completeness=_OK_CHECK,
        source_quality=_OK_CHECK,
    )


@pytest.mark.parametrize(
    "final_verdict,claim_domain,expected_severity",
    [
        ("contradicted", "financial", "critical"),
        ("contradicted", "legal", "major"),
        ("disputed", "financial", "major"),
        ("disputed", "legal", "major"),
        ("insufficient", "financial", "minor"),
        ("insufficient", None, "minor"),
        ("supported", "financial", "info"),
        ("supported", "general", "info"),
    ],
)
def test_severity_table(final_verdict, claim_domain, expected_severity):
    assert derive_severity(final_verdict, claim_domain) == expected_severity


def test_agreement_uses_min_confidence_and_verifiers_verdict():
    verifier = _verifier("supported", confidence=0.9)
    challenger = _challenger("supported", accepted_verdict="supported", confidence=0.7)

    result = reconcile(verifier, challenger, claim_domain="financial")

    assert result["agreement"] is True
    assert result["final_verdict"] == "supported"
    assert result["final_confidence"] == 0.7
    assert result["severity"] == "info"
    assert result["resolved_by"] == "agent"


def test_disagreement_never_silently_resolves_to_either_agents_answer():
    verifier = _verifier("supported", confidence=0.9)
    challenger = _challenger("contradicted", accepted_verdict="contradicted", confidence=0.85)

    result = reconcile(verifier, challenger, claim_domain="financial")

    assert result["agreement"] is False
    assert result["final_verdict"] == "disputed"
    assert result["final_verdict"] != verifier.verdict
    assert result["final_verdict"] != challenger.verdict
    assert result["final_confidence"] == 0.0
    assert result["severity"] == "major"


def test_disagreement_severity_is_major_for_any_domain():
    verifier = _verifier("supported")
    challenger = _challenger("insufficient", accepted_verdict="insufficient")

    result = reconcile(verifier, challenger, claim_domain="general")

    assert result["final_verdict"] == "disputed"
    assert result["severity"] == "major"
