from agno.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim
from app.retrieval.internal_index import lookup_internal_evidence


def make_internal_lookup_tool(session: AsyncSession, claim: Claim):
    """Wraps Phase 5.1's internal lookup as a callable tool an agent can invoke mid-reasoning if
    it wants to re-check the document's own tables/chunks itself, beyond the evidence bundle
    already retrieved for it. Bound to this session+claim via closure since Agno tools are plain
    callables with no built-in way to inject per-call context."""

    @tool(
        name="internal_lookup",
        description="Re-check the document's own tables/chunks directly for this claim's evidence.",
    )
    async def internal_lookup() -> str:
        evidence = await lookup_internal_evidence(session, claim)
        if not evidence:
            return "No internal evidence found for this claim's requirements."
        return "\n".join(f"[{e.source_type}] {e.source_ref}: {e.content_snippet}" for e in evidence)

    return internal_lookup
