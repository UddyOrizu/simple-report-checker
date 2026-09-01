import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass

from agno.agent import Agent
from agno.models.huggingface import HuggingFace

from app.agents.reconcile import derive_severity
from app.llm.client import MissingCredentialsError, build_model, load_prompt
from app.schemas.verification import VerdictValue, VerifierResult

# Which models sit on the internal-claim vote panel, in order. Configurable so a deployment can
# swap in different specialized models without a code change; kept to 3 by default so a majority
# always exists on a 3-way split (a 2-voter panel can't break a tie). "standard"/"mini" use
# whichever LLM_PROVIDER is active (see app.llm.client) — the fixed third voter, "fino1", is a
# genuinely different model (TheFinAI/Fin-o1-8B, a finance-domain reasoning model) rather than
# another call to the same vendor, since two votes from one provider aren't independent evidence
# the way a distinct model's vote is.
INTERNAL_VERIFICATION_VOTERS = [v.strip() for v in os.getenv("INTERNAL_VERIFICATION_VOTERS", "standard,mini,fino1").split(",") if v.strip()]

FINO1_MODEL_ID = os.getenv("FINO1_MODEL_ID", "TheFinAI/Fin-o1-8B")
# Unset by default: a dedicated HF Inference Endpoint URL, if the model is deployed that way
# rather than through HF's shared serverless Inference Providers routing.
HF_INFERENCE_BASE_URL = os.getenv("HF_INFERENCE_BASE_URL") or None

_INSTRUCTIONS = load_prompt("verifier")

# Fin-o1-8B has no native structured-output/tool-calling support the way OpenAI/Anthropic do
# (agno.models.huggingface.HuggingFace reports supports_native_structured_outputs=False), so its
# vote is elicited as plain text with an explicit JSON-shape instruction and parsed by hand,
# rather than relying on Agent's output_schema machinery.
_JSON_OUTPUT_SUFFIX = """

Respond with ONLY a single JSON object, no markdown code fences and no text before or after it,
in exactly this shape:
{"verdict": "supported" | "contradicted" | "insufficient", "confidence": <float 0-1>, "reasoning": "<string>"}
"""


@dataclass
class VoteOutcome:
    voter: str
    verdict: VerdictValue | None
    confidence: float | None
    reasoning: str
    prompt_sent: str = ""
    raw_response: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {"voter": self.voter, "verdict": self.verdict, "confidence": self.confidence, "reasoning": self.reasoning, "error": self.error}


def _format_prompt(claim_text: str, claim_type: str, scope: str, evidence_bundle: str) -> str:
    return _INSTRUCTIONS.format(claim_text=claim_text, claim_type=claim_type, scope=scope, evidence_bundle=evidence_bundle)


async def _vote_structured(voter: str, tier: str, prompt: str) -> VoteOutcome:
    """standard/mini voters — whichever provider LLM_PROVIDER selects, using agno's native
    structured-output support."""
    agent = Agent(model=build_model(tier), output_schema=VerifierResult, markdown=False)
    response = await agent.arun(prompt)
    result: VerifierResult = response.content
    return VoteOutcome(
        voter=voter,
        verdict=result.verdict,
        confidence=result.confidence,
        reasoning=result.reasoning,
        prompt_sent=prompt,
        raw_response=result.model_dump_json(),
    )


def _parse_json_verdict(raw: str) -> tuple[VerdictValue, float, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    data = json.loads(text)
    verdict = data["verdict"]
    if verdict not in ("supported", "contradicted", "insufficient"):
        raise ValueError(f"unrecognized verdict {verdict!r}")
    confidence = max(0.0, min(1.0, float(data["confidence"])))
    reasoning = str(data.get("reasoning", ""))
    return verdict, confidence, reasoning


async def _vote_fino1(prompt: str) -> VoteOutcome:
    if not os.getenv("HF_TOKEN"):
        raise MissingCredentialsError("HF_TOKEN is not set — the fino1 voter is BLOCKED-CREDENTIALS")

    
    model = HuggingFace(id=FINO1_MODEL_ID, api_key=os.getenv("HF_TOKEN"), base_url=HF_INFERENCE_BASE_URL)
    agent = Agent(model=model, markdown=False)
    full_prompt = prompt + _JSON_OUTPUT_SUFFIX
    response = await agent.arun(full_prompt)
    raw = str(response.content)
    verdict, confidence, reasoning = _parse_json_verdict(raw)
    return VoteOutcome(voter="fino1", verdict=verdict, confidence=confidence, reasoning=reasoning, prompt_sent=full_prompt, raw_response=raw)


async def _run_voter(voter: str, claim_text: str, claim_type: str, scope: str, evidence_bundle: str) -> VoteOutcome:
    prompt = _format_prompt(claim_text, claim_type, scope, evidence_bundle)
    try:
        if voter == "standard":
            return await _vote_structured("standard", "standard", prompt)
        if voter == "mini":
            return await _vote_structured("mini", "mini", prompt)
        if voter == "fino1":
            return await _vote_fino1(prompt)
        raise ValueError(f"unknown voter {voter!r} in INTERNAL_VERIFICATION_VOTERS")
    except Exception as exc:  # noqa: BLE001 - one voter's failure must not sink the whole panel
        return VoteOutcome(voter=voter, verdict=None, confidence=None, reasoning="", prompt_sent=prompt, error=str(exc))


async def run_internal_vote_panel(claim_text: str, claim_type: str, scope: str, evidence_bundle: str) -> list[VoteOutcome]:
    """Runs every configured voter concurrently against the same claim + evidence bundle. Each
    voter is isolated in its own try/except (see _run_voter) so a missing HF_TOKEN or an
    unreachable specialized model degrades the panel to fewer voters rather than failing the
    whole verification — the same graceful-degradation contract every other LLM-dependent stage
    in this pipeline follows."""
    return await asyncio.gather(*(_run_voter(v, claim_text, claim_type, scope, evidence_bundle) for v in INTERNAL_VERIFICATION_VOTERS))


def tally_votes(votes: list[VoteOutcome], claim_domain: str | None) -> dict:
    """Highest-vote reconciliation: the verdict with the most voters wins, using the minimum
    confidence among the agreeing voters (the same conservative, not-averaged, aggregation
    app.agents.reconcile uses for the two-agent adversarial pipeline). A tie between verdicts —
    including the all-disagree case on a 3-voter panel — resolves to "disputed" at zero
    confidence rather than picking one arbitrarily, mirroring reconcile()'s own philosophy that a
    disagreement must never silently collapse to a single answer."""
    successful = [v for v in votes if v.error is None]

    if not successful:
        failures = "; ".join(f"{v.voter}: {v.error}" for v in votes) or "no voters configured"
        return {
            "final_verdict": "insufficient",
            "final_confidence": 0.0,
            "agreement": None,
            "severity": derive_severity("insufficient", claim_domain),
            "resolved_by": "ensemble_vote",
            "voter_breakdown": [v.to_dict() for v in votes],
            "reasoning": f"No voter completed successfully — {failures}.",
        }

    counts = Counter(v.verdict for v in successful)
    top_count = max(counts.values())
    winners = [verdict for verdict, c in counts.items() if c == top_count]

    if len(winners) > 1:
        final_verdict = "disputed"
        final_confidence = 0.0
        agreement = False
        reasoning = f"No majority verdict — votes split {dict(counts)}."
    else:
        final_verdict = winners[0]
        agreeing = [v for v in successful if v.verdict == final_verdict]
        final_confidence = min(v.confidence for v in agreeing)
        agreement = len(agreeing) == len(successful)
        reasoning = f"{len(agreeing)}/{len(successful)} voters agreed on '{final_verdict}'. " + " | ".join(
            f"{v.voter}: {v.reasoning}" for v in agreeing
        )

    return {
        "final_verdict": final_verdict,
        "final_confidence": final_confidence,
        "agreement": agreement,
        "severity": derive_severity(final_verdict, claim_domain),
        "resolved_by": "ensemble_vote",
        "voter_breakdown": [v.to_dict() for v in votes],
        "reasoning": reasoning,
    }
