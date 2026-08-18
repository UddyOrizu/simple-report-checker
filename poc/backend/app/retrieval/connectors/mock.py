import logging

from app.models import Claim, Evidence

logger = logging.getLogger(__name__)


class MockConnector:
    """Stands in for a real external-evidence source (e.g. a competitor-filing search API) that
    this POC doesn't wire up to a live source for. Every result is realistic fixture data, but
    logged clearly as a mock so it's never mistaken for a real external lookup downstream."""

    async def fetch(self, claim: Claim, config: dict) -> list[Evidence]:
        logger.info(
            "MockConnector: returning MOCK external evidence for claim %s — not a real external source", claim.id
        )
        return [
            Evidence(
                claim_id=claim.id,
                source_type="external",
                source_ref="mock://competitor-filing",
                content_snippet="Competitor reported 8% revenue growth",
                authority_score=0.6,
            )
        ]
