import json
import os
import uuid

from sqlalchemy import select

from app.agents.verify_claim import verify_claim
from app.config_hash import compute_config_hash
from app.db import async_session
from app.models import Claim

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts", ".run_snapshots")


def snapshot_path(document_id: str, config_hash: str) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{document_id}__{config_hash}.json")


async def take_snapshot(document_id: uuid.UUID, thresholds: dict, registry: list[dict]) -> str:
    """Re-verifies every existing claim for `document_id` under the current on-disk config and
    saves the results to a snapshot file named by the current config hash. Claims/verdicts
    aren't tagged with config_hash in the schema (only agent_traces/pipeline_runs are), so this
    snapshot file — not a DB query — is the actual unit of comparison for 8.3."""
    config_hash = compute_config_hash()

    async with async_session() as session:
        claims = (await session.execute(select(Claim).where(Claim.document_id == document_id))).scalars().all()

        results = []
        for claim in claims:
            outcome = await verify_claim(session, claim, config={}, thresholds=thresholds, registry=registry)
            if outcome is None:
                continue
            results.append(
                {
                    "claim_id": str(claim.id),
                    "claim_text": claim.claim_text,
                    "domain": claim.domain,
                    "scope": claim.scope,
                    "verdict": outcome["reconciled"]["final_verdict"],
                    "confidence": outcome["reconciled"]["final_confidence"],
                }
            )

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = snapshot_path(str(document_id), config_hash)
    with open(path, "w") as f:
        json.dump({"document_id": str(document_id), "config_hash": config_hash, "claims": results}, f, indent=2)

    return path


def load_snapshot(document_id: str, config_hash: str) -> dict:
    path = snapshot_path(document_id, config_hash)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No snapshot for document={document_id} config_hash={config_hash} — take a snapshot under that config first."
        )
    with open(path) as f:
        return json.load(f)


def diff_snapshots(snapshot_a: dict, snapshot_b: dict) -> list[dict]:
    """Claim-by-claim diff of verdict/confidence/domain/scope between two snapshots. Claims
    present in only one snapshot (e.g. re-extraction changed the claim set) aren't meaningful
    verdict diffs and are skipped."""
    by_id_a = {c["claim_id"]: c for c in snapshot_a["claims"]}
    by_id_b = {c["claim_id"]: c for c in snapshot_b["claims"]}

    diffs = []
    for claim_id in sorted(set(by_id_a) & set(by_id_b)):
        a, b = by_id_a[claim_id], by_id_b[claim_id]
        changed = {field: [a[field], b[field]] for field in ("verdict", "confidence", "domain", "scope") if a[field] != b[field]}
        if changed:
            diffs.append({"claim_id": claim_id, "claim_text": a["claim_text"], "changes": changed})
    return diffs
