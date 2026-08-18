from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim, Evidence
from app.retrieval.connectors.mock import MockConnector
from app.retrieval.cross_reference import resolve_cross_reference
from app.retrieval.internal_index import lookup_internal_evidence

external_connector = MockConnector()


async def dispatch_retrieval(session: AsyncSession, claim: Claim, config: dict) -> list[Evidence]:
    """Reads claim.scope and calls exactly the retrieval path(s) that scope implies — this is
    what makes Phase 3's scope tag actually consequential, not just metadata. `internal`/`both`
    try the direct lookup first, falling back to the cross-reference navigator only on a miss;
    `external`/`both` hit the (mock) external connector; `internal` alone never touches it."""
    evidence: list[Evidence] = []

    if claim.scope in ("internal", "both"):
        internal_evidence = await lookup_internal_evidence(session, claim)
        if internal_evidence:
            evidence.extend(internal_evidence)
        else:
            cross_ref_evidence = await resolve_cross_reference(session, claim)
            if cross_ref_evidence is not None:
                evidence.append(cross_ref_evidence)

    if claim.scope in ("external", "both"):
        evidence.extend(await external_connector.fetch(claim, config))

    return evidence
