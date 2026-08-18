import os
import uuid

import yaml
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.agents.verify_claim import verify_claim
from app.db import async_session
from app.llm.client import MissingCredentialsError
from app.models import AgentTrace, Claim, Evidence, Verdict

router = APIRouter()

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")


def _load_yaml(name: str):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return yaml.safe_load(f)


def _verdict_dict(verdict: Verdict | None) -> dict | None:
    if verdict is None:
        return None
    return {
        "verifier_verdict": verdict.verifier_verdict,
        "verifier_confidence": verdict.verifier_confidence,
        "verifier_reasoning": verdict.verifier_reasoning,
        "challenger_verdict": verdict.challenger_verdict,
        "challenger_confidence": verdict.challenger_confidence,
        "challenger_reasoning": verdict.challenger_reasoning,
        "agreement": verdict.agreement,
        "final_verdict": verdict.final_verdict,
        "final_confidence": verdict.final_confidence,
        "severity": verdict.severity,
        "resolved_by": verdict.resolved_by,
        "resolved_at": verdict.resolved_at.isoformat(),
    }


def _evidence_dict(evidence: Evidence) -> dict:
    return {
        "id": str(evidence.id),
        "source_type": evidence.source_type,
        "source_ref": evidence.source_ref,
        "content_snippet": evidence.content_snippet,
        "authority_score": evidence.authority_score,
        "retrieved_at": evidence.retrieved_at.isoformat(),
    }


def _trace_dict(trace: AgentTrace) -> dict:
    return {
        "id": str(trace.id),
        "agent_name": trace.agent_name,
        "prompt_sent": trace.prompt_sent,
        "raw_response": trace.raw_response,
        "tool_calls": trace.tool_calls,
        "config_hash": trace.config_hash,
        "created_at": trace.created_at.isoformat(),
    }


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: uuid.UUID) -> dict:
    """Single claim: verdict, evidence, and full agent traces — the detailed view for a claim's
    review panel."""
    async with async_session() as session:
        claim = await session.get(Claim, claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="Claim not found")

        verdict = (
            await session.execute(select(Verdict).where(Verdict.claim_id == claim_id).order_by(Verdict.resolved_at.desc()))
        ).scalars().first()
        evidence = (await session.execute(select(Evidence).where(Evidence.claim_id == claim_id))).scalars().all()
        traces = (await session.execute(select(AgentTrace).where(AgentTrace.claim_id == claim_id))).scalars().all()

        return {
            "id": str(claim.id),
            "document_id": str(claim.document_id),
            "claim_text": claim.claim_text,
            "source_span": claim.source_span,
            "claim_type": claim.claim_type,
            "scope": claim.scope,
            "requires": claim.requires,
            "domain": claim.domain,
            "domain_confidence": claim.domain_confidence,
            "domain_source": claim.domain_source,
            "verdict": _verdict_dict(verdict),
            "evidence": [_evidence_dict(e) for e in evidence],
            "traces": [_trace_dict(t) for t in traces],
        }


@router.get("/claims/{claim_id}/traces")
async def get_claim_traces(claim_id: uuid.UUID) -> list[dict]:
    """Raw agent_traces rows for a claim."""
    async with async_session() as session:
        traces = (await session.execute(select(AgentTrace).where(AgentTrace.claim_id == claim_id))).scalars().all()
        return [_trace_dict(t) for t in traces]


@router.post("/claims/{claim_id}/reverify")
async def reverify_claim(claim_id: uuid.UUID) -> dict:
    """Re-runs verification on one claim against the current config, without reprocessing the
    document — produces exactly one new pair of agent_traces rows (or one new deterministic
    verdicts row) and touches no other claim."""
    thresholds = _load_yaml("thresholds.yaml")
    registry = _load_yaml("domain_registry.yaml")

    async with async_session() as session:
        claim = await session.get(Claim, claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="Claim not found")

        try:
            result = await verify_claim(session, claim, config={}, thresholds=thresholds, registry=registry)
        except MissingCredentialsError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    return {"claim_id": str(claim_id), "reconciled": result["reconciled"]}
