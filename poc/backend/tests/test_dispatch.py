import uuid
from unittest.mock import AsyncMock, patch

from app.models import Claim, Evidence
from app.retrieval.dispatch import dispatch_retrieval


def _claim(scope: str) -> Claim:
    return Claim(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        claim_text="x",
        source_span="x",
        claim_type="statistical",
        scope=scope,
        requires=["x"],
    )


def _evidence(claim: Claim, source_type: str) -> Evidence:
    return Evidence(claim_id=claim.id, source_type=source_type, source_ref="ref", content_snippet="x")


async def test_internal_scope_calls_only_internal_lookup_when_it_hits():
    claim = _claim("internal")
    with (
        patch("app.retrieval.dispatch.lookup_internal_evidence", new_callable=AsyncMock) as mock_internal,
        patch("app.retrieval.dispatch.resolve_cross_reference", new_callable=AsyncMock) as mock_cross_ref,
        patch("app.retrieval.dispatch.external_connector.fetch", new_callable=AsyncMock) as mock_external,
    ):
        mock_internal.return_value = [_evidence(claim, "internal_table")]

        evidence = await dispatch_retrieval(session=None, claim=claim, config={})

    mock_internal.assert_called_once()
    mock_cross_ref.assert_not_called()  # 5.1 hit — no need for the fallback
    mock_external.assert_not_called()  # internal scope never touches the external connector
    assert len(evidence) == 1


async def test_internal_scope_falls_back_to_cross_reference_only_on_a_miss():
    claim = _claim("internal")
    with (
        patch("app.retrieval.dispatch.lookup_internal_evidence", new_callable=AsyncMock) as mock_internal,
        patch("app.retrieval.dispatch.resolve_cross_reference", new_callable=AsyncMock) as mock_cross_ref,
        patch("app.retrieval.dispatch.external_connector.fetch", new_callable=AsyncMock) as mock_external,
    ):
        mock_internal.return_value = []  # 5.1 misses
        mock_cross_ref.return_value = _evidence(claim, "internal_table")

        evidence = await dispatch_retrieval(session=None, claim=claim, config={})

    mock_internal.assert_called_once()
    mock_cross_ref.assert_called_once()  # only invoked because 5.1 missed
    mock_external.assert_not_called()
    assert len(evidence) == 1


async def test_external_scope_calls_only_external_connector():
    claim = _claim("external")
    with (
        patch("app.retrieval.dispatch.lookup_internal_evidence", new_callable=AsyncMock) as mock_internal,
        patch("app.retrieval.dispatch.resolve_cross_reference", new_callable=AsyncMock) as mock_cross_ref,
        patch("app.retrieval.dispatch.external_connector.fetch", new_callable=AsyncMock) as mock_external,
    ):
        mock_external.return_value = [_evidence(claim, "external")]

        evidence = await dispatch_retrieval(session=None, claim=claim, config={})

    mock_internal.assert_not_called()  # external scope never touches internal lookup
    mock_cross_ref.assert_not_called()
    mock_external.assert_called_once()
    assert len(evidence) == 1


async def test_both_scope_calls_both_sides():
    claim = _claim("both")
    with (
        patch("app.retrieval.dispatch.lookup_internal_evidence", new_callable=AsyncMock) as mock_internal,
        patch("app.retrieval.dispatch.resolve_cross_reference", new_callable=AsyncMock) as mock_cross_ref,
        patch("app.retrieval.dispatch.external_connector.fetch", new_callable=AsyncMock) as mock_external,
    ):
        mock_internal.return_value = [_evidence(claim, "internal_table")]
        mock_external.return_value = [_evidence(claim, "external")]

        evidence = await dispatch_retrieval(session=None, claim=claim, config={})

    mock_internal.assert_called_once()
    mock_cross_ref.assert_not_called()  # internal side hit, so no fallback needed
    mock_external.assert_called_once()
    assert len(evidence) == 2
