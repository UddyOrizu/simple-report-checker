import logging
import uuid

from app.models import Claim
from app.retrieval.connectors.mock import MockConnector


def _claim(scope: str) -> Claim:
    return Claim(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        claim_text="we outperform our closest competitor",
        source_span="ahead of our closest competitor",
        claim_type="comparative",
        scope=scope,
        requires=["competitor revenue figure"],
    )


async def test_mock_connector_returns_realistic_evidence():
    connector = MockConnector()
    claim = _claim("external")

    evidence = await connector.fetch(claim, config={})

    assert len(evidence) == 1
    assert evidence[0].source_type == "external"
    assert evidence[0].source_ref.startswith("mock://")
    assert evidence[0].claim_id == claim.id
    assert "8% revenue growth" in evidence[0].content_snippet


async def test_mock_connector_logs_clearly_that_it_is_a_mock(caplog):
    connector = MockConnector()
    claim = _claim("both")

    with caplog.at_level(logging.INFO):
        await connector.fetch(claim, config={})

    assert any("mock" in record.message.lower() for record in caplog.records)
