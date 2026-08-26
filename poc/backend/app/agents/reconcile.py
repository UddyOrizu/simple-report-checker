from app.schemas.verification import ChallengerResult, VerifierResult


def derive_severity(final_verdict: str, claim_domain: str | None) -> str:
    if final_verdict == "contradicted":
        return "critical" if claim_domain == "financial" else "major"
    if final_verdict in ("unverifiable", "disputed"):
        return "major"
    if final_verdict in ("partially_supported", "insufficient"):
        return "minor"
    if final_verdict == "supported":
        return "info"
    raise ValueError(f"unknown final_verdict: {final_verdict!r}")


def reconcile(verifier_result: VerifierResult, challenger_result: ChallengerResult, claim_domain: str | None) -> dict:
    """Agreement means the challenger accepted the verifier's verdict (accepted_verdict ==
    verifier_result.verdict); anything else resolves to disputed at zero confidence — a
    disagreement never silently collapses to either agent's individual answer."""
    agreement = verifier_result.verdict == challenger_result.accepted_verdict

    if agreement:
        final_verdict = verifier_result.verdict
        final_confidence = min(verifier_result.confidence, challenger_result.confidence)
    else:
        final_verdict = "disputed"
        final_confidence = 0.0

    return {
        "final_verdict": final_verdict,
        "final_confidence": final_confidence,
        "agreement": agreement,
        "severity": derive_severity(final_verdict, claim_domain),
        "resolved_by": "agent",
    }
