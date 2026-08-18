from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.challenger import run_challenger
from app.agents.deterministic_verifier import resolves_deterministically, verify_deterministic
from app.agents.reconcile import derive_severity, reconcile
from app.agents.verifier import run_verifier
from app.config_hash import compute_config_hash
from app.models import AgentTrace, Claim, Verdict
from app.retrieval.dispatch import dispatch_retrieval


def _deterministic_reasoning(outcome: dict, thresholds: dict) -> str:
    """The recomputed figures behind a deterministic verdict, stored on Verdict.verifier_reasoning
    (repurposed for this path — resolved_by='deterministic' tells callers there's no real
    "verifier" involved) so the review panel can show *why*, not just the verdict, on any later
    GET /claims/{id} — not only in reverify's own immediate response."""
    if "computed_pct" not in outcome:
        return outcome.get("reasoning", "")
    return (
        f"Recomputed (current - prior) / prior = {outcome['computed_pct']:.2f}%, "
        f"vs. the claim's stated {outcome['stated_pct']:.2f}% "
        f"(difference {outcome['difference']:.2f} points, tolerance {thresholds['arithmetic_tolerance_pct']} points)."
    )


async def verify_claim(session: AsyncSession, claim: Claim, config: dict, thresholds: dict, registry: list[dict]) -> dict:
    """Routes a claim to the deterministic path (no LLM) or the agent path, per
    domain_registry.yaml's verification_method for its (domain, claim_type) pair — the actual
    place that registry routing decision takes effect."""
    if resolves_deterministically(claim.domain, claim.claim_type, registry):
        outcome = await verify_deterministic(session, claim, thresholds)
        severity = derive_severity(outcome["final_verdict"], claim.domain)
        final_confidence = 1.0 if outcome["final_verdict"] in ("supported", "contradicted") else 0.0
        for evidence_item in outcome.get("evidence", []):
            session.add(evidence_item)
        session.add(
            Verdict(
                claim_id=claim.id,
                verifier_reasoning=_deterministic_reasoning(outcome, thresholds),
                final_verdict=outcome["final_verdict"],
                final_confidence=final_confidence,
                severity=severity,
                resolved_by="deterministic",
            )
        )
        await session.commit()
        evidence = outcome.get("evidence", [])
        outcome_without_evidence = {k: v for k, v in outcome.items() if k != "evidence"}
        reconciled = {**outcome_without_evidence, "severity": severity, "final_confidence": final_confidence}
        return {"reconciled": reconciled, "evidence": evidence}

    return await verify_claim_via_agents(session, claim, config)


async def verify_claim_via_agents(session: AsyncSession, claim: Claim, config: dict) -> dict:
    """Runs the verifier, then the challenger (given the verifier's result), reconciles the two,
    and persists an agent_traces row per call plus the final verdicts row — the full Phase 6.5
    tracing wiring, reused by both scripts/run_verify.py and any future API endpoint."""
    evidence = await dispatch_retrieval(session, claim, config)
    for evidence_item in evidence:
        session.add(evidence_item)

    verifier_result, verifier_prompt, verifier_raw, verifier_tools = await run_verifier(session, claim, evidence)
    challenger_result, challenger_prompt, challenger_raw, challenger_tools = await run_challenger(
        session, claim, evidence, verifier_result
    )

    reconciled = reconcile(verifier_result, challenger_result, claim.domain)
    config_hash = compute_config_hash()

    session.add(
        AgentTrace(
            claim_id=claim.id,
            agent_name="verifier",
            prompt_sent=verifier_prompt,
            raw_response=verifier_raw,
            tool_calls=verifier_tools,
            config_hash=config_hash,
        )
    )
    session.add(
        AgentTrace(
            claim_id=claim.id,
            agent_name="challenger",
            prompt_sent=challenger_prompt,
            raw_response=challenger_raw,
            tool_calls=challenger_tools,
            config_hash=config_hash,
        )
    )
    session.add(
        Verdict(
            claim_id=claim.id,
            verifier_verdict=verifier_result.verdict,
            verifier_confidence=verifier_result.confidence,
            verifier_reasoning=verifier_result.reasoning,
            challenger_verdict=challenger_result.verdict,
            challenger_confidence=challenger_result.confidence,
            challenger_reasoning=challenger_result.reasoning,
            agreement=reconciled["agreement"],
            final_verdict=reconciled["final_verdict"],
            final_confidence=reconciled["final_confidence"],
            severity=reconciled["severity"],
            resolved_by=reconciled["resolved_by"],
        )
    )
    await session.commit()

    return {"verifier": verifier_result, "challenger": challenger_result, "reconciled": reconciled, "evidence": evidence}
