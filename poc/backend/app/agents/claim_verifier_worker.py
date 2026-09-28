import asyncio
import logging
import os
import uuid
import yaml

from sqlalchemy import select, update

from app.db import async_session
from app.agents.verify_claim import verify_claim
from app.models import Claim, Verdict
from app.ingestion.pipeline import load_config

logger = logging.getLogger(__name__)

# How many claims to verify concurrently in the background worker
VERIFICATION_CONCURRENCY = 4
# Poll interval when no pending claims found
POLL_INTERVAL_SECONDS = 3

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")


def _load_yaml(name: str):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return yaml.safe_load(f)


async def _claims_pending_verification(limit: int = 10) -> list[Claim]:
    async with async_session() as session:
        # Select claims that don't yet have a verdict and are still pending
        already_verified = select(Verdict.claim_id)
        stmt = select(Claim).where(Claim.status == "pending", Claim.id.not_in(already_verified)).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        # Mark them as 'verifying' to avoid duplicate workers picking them up
        if rows:
            ids = [r.id for r in rows]
            await session.execute(update(Claim).where(Claim.id.in_(ids)).values(status="verifying"))
            await session.commit()
        return rows


async def _verify_one(claim_stub: Claim, config: dict, thresholds: dict, registry: list[dict]) -> None:
    async with async_session() as session:
        claim = await session.get(Claim, claim_stub.id)
        try:
            result = await verify_claim(session, claim, config=config, thresholds=thresholds, registry=registry)
        except Exception:
            logger.exception("Verification failed for claim %s", claim.id)
            # mark claim back to pending so it can be retried
            async with async_session() as s2:
                c = await s2.get(Claim, claim.id)
                c.status = "pending"
                await s2.commit()
            return

        # mark claim as verified (or leave pending if result is None)
        async with async_session() as s3:
            c = await s3.get(Claim, claim.id)
            if result is None:
                c.status = "skipped"
            else:
                c.status = "verified"
            await s3.commit()


async def run_background_verifier(stop_event: asyncio.Event | None = None) -> None:
    """Continuously polls for pending claims and verifies them."""
    config = load_config()
    thresholds = _load_yaml("thresholds.yaml")
    registry = _load_yaml("domain_registry.yaml")

    stop_event = stop_event or asyncio.Event()
    sem = asyncio.Semaphore(VERIFICATION_CONCURRENCY)
    try:
        while not stop_event.is_set():
            pending = await _claims_pending_verification(limit=VERIFICATION_CONCURRENCY * 2)
            if not pending:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            async def _worker(claim_stub):
                async with sem:
                    await _verify_one(claim_stub, config, thresholds, registry)

            tasks = [asyncio.create_task(_worker(c)) for c in pending]
            # wait for this batch to finish but don't let one failure stop the loop
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException):
                    logger.error("Background verifier task error: %s", r)

    except asyncio.CancelledError:
        logger.info("Background verifier cancelled")
    except Exception:
        logger.exception("Background verifier crashed unexpectedly")
