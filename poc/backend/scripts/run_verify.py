"""CLI: python scripts/run_verify.py --adhoc "claim text" --evidence "evidence text"
       python scripts/run_verify.py --claim-id <uuid>

Runs the verifier + challenger agents against either an ad-hoc claim/evidence pair or a stored
claim (evidence retrieved via Phase 5's dispatcher), reconciles the results, and prints both
agents' full reasoning and tool calls.
"""

import argparse
import asyncio
import json
import uuid

from app.agents.challenger import run_challenger
from app.agents.reconcile import reconcile
from app.agents.verifier import run_verifier
from app.db import async_session
from app.models import Claim, Evidence
from app.retrieval.dispatch import dispatch_retrieval


class AdhocClaim:
    """A throwaway claim-like object for --adhoc mode, which has no real DB row — never
    persisted, just enough attribute surface for the agents to run against."""

    def __init__(self, text: str):
        self.id = uuid.uuid4()
        self.claim_text = text
        self.claim_type = "statistical"
        self.scope = "internal"
        self.domain = None
        self.requires: list[str] = []


async def run_adhoc(claim_text: str, evidence_text: str) -> None:
    claim = AdhocClaim(claim_text)
    evidence = [Evidence(claim_id=claim.id, source_type="adhoc", source_ref="cli", content_snippet=evidence_text)]
    await _run_and_print(None, claim, evidence)


async def run_stored(claim_id: uuid.UUID) -> None:
    async with async_session() as session:
        claim = await session.get(Claim, claim_id)
        if claim is None:
            print(f"No claim found with id {claim_id}")
            return
        evidence = await dispatch_retrieval(session, claim, config={})
        await _run_and_print(session, claim, evidence)


async def _run_and_print(session, claim, evidence: list[Evidence]) -> None:
    verifier_result, _, _, verifier_tools = await run_verifier(session, claim, evidence)
    print("=== Verifier ===")
    print(f"verdict={verifier_result.verdict}  confidence={verifier_result.confidence:.2f}")
    print(f"reasoning: {verifier_result.reasoning}")
    print(f"citations: {verifier_result.citations}")
    print(f"tool calls: {json.dumps(verifier_tools, indent=2)}")
    print()

    challenger_result, _, _, challenger_tools = await run_challenger(session, claim, evidence, verifier_result)
    print("=== Challenger ===")
    print(f"verdict={challenger_result.verdict}  accepted_verdict={challenger_result.accepted_verdict}  confidence={challenger_result.confidence:.2f}")
    print(f"reasoning: {challenger_result.reasoning}")
    print(f"tool calls: {json.dumps(challenger_tools, indent=2)}")
    print()

    reconciled = reconcile(verifier_result, challenger_result, getattr(claim, "domain", None))
    print("=== Reconciled ===")
    print(json.dumps(reconciled, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run verifier + challenger against a claim.")
    parser.add_argument("--adhoc", help="Ad-hoc claim text (pair with --evidence)")
    parser.add_argument("--evidence", help="Ad-hoc evidence text (pair with --adhoc)")
    parser.add_argument("--claim-id", help="A stored claim's UUID — evidence via Phase 5's dispatcher")
    args = parser.parse_args()

    if args.adhoc:
        asyncio.run(run_adhoc(args.adhoc, args.evidence or ""))
    elif args.claim_id:
        asyncio.run(run_stored(uuid.UUID(args.claim_id)))
    else:
        parser.error("must supply either --adhoc (with --evidence) or --claim-id")


if __name__ == "__main__":
    main()
