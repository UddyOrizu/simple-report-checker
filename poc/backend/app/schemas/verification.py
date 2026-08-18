from typing import Literal

from pydantic import BaseModel

VerdictValue = Literal["supported", "contradicted", "insufficient"]


class VerifierResult(BaseModel):
    verdict: VerdictValue
    confidence: float
    reasoning: str
    citations: list[str]


class ChallengerResult(BaseModel):
    verdict: VerdictValue  # the challenger's own independent verdict
    accepted_verdict: VerdictValue  # the verdict that stands: the verifier's if accepted, the challenger's own if rejected
    confidence: float
    reasoning: str
