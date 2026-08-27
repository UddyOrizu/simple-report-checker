from typing import Literal

from pydantic import BaseModel, Field

VerdictValue = Literal["supported", "contradicted", "insufficient"]


class VerifierResult(BaseModel):
    verdict: VerdictValue
    confidence: float
    reasoning: str
    citations: list[str]


class ChallengerCheck(BaseModel):
    """One of the challenger's four independent checks. Each must stand on its own — a reader
    should be able to understand this check's finding without reading the other three, which a
    single blended `reasoning` paragraph doesn't guarantee (the model can give a hard check a
    token mention while the easy, tool-assisted one dominates the response)."""

    ok: bool = Field(..., description="True if this check found no problem with the Verifier's conclusion.")
    reasoning: str = Field(..., description="What was checked and what was found — must stand on its own.")


class ChallengerResult(BaseModel):
    citation_fidelity: ChallengerCheck = Field(
        ..., description="Does the evidence the Verifier cited actually say what the Verifier claims it says?"
    )
    basis_match: ChallengerCheck = Field(
        ..., description="Same basis/definition and time period as the claim, not just a superficially similar figure?"
    )
    completeness: ChallengerCheck = Field(
        ..., description="Does the evidence support the FULL claim, or only part of it?"
    )
    source_quality: ChallengerCheck = Field(
        ...,
        description="For external evidence: current, authoritative, corroborated by more than one source? "
        "ok=true (not applicable) when the claim's evidence is entirely internal.",
    )
    verdict: VerdictValue  # the challenger's own independent verdict
    accepted_verdict: VerdictValue  # the verdict that stands: the verifier's if accepted, the challenger's own if rejected
    confidence: float

    def compose_reasoning(self) -> str:
        """One persisted reasoning string built from the four independent checks, for callers
        (the verdicts table's single-text challenger_reasoning column, the review UI) that expect
        one field — composed in code from checks the model was forced to answer independently,
        rather than asking the model to write a single blended paragraph in the first place."""
        checks = [
            ("Citation fidelity", self.citation_fidelity),
            ("Basis/definition match", self.basis_match),
            ("Completeness", self.completeness),
            ("Source quality", self.source_quality),
        ]
        return " ".join(f"{label} [{'OK' if check.ok else 'FAILED'}]: {check.reasoning}" for label, check in checks)
