import os

import pytest_asyncio
import yaml

from app.agents.deterministic_verifier import (
    extract_current_prior,
    parse_money,
    parse_stated_percent,
    resolves_deterministically,
    verify_deterministic,
)
from app.models import Claim, Document, DocumentChunk, ExtractedTable


def _thresholds():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "thresholds.yaml")
    return yaml.safe_load(open(path))


def _registry():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")
    return yaml.safe_load(open(path))


async def _make_claim(db_session, *, table_data, claim_text, requires):
    document = Document(filename="sample_report.docx", file_type="docx", storage_path="x", status="ingested")
    db_session.add(document)
    await db_session.flush()

    db_session.add(ExtractedTable(document_id=document.id, page_number=1, table_data=table_data))

    origin_chunk = DocumentChunk(
        document_id=document.id, chunk_type="paragraph", chunk_text=claim_text, page_number=1
    )
    db_session.add(origin_chunk)
    await db_session.flush()

    claim = Claim(
        document_id=document.id,
        chunk_id=origin_chunk.id,
        claim_text=claim_text,
        source_span=claim_text,
        claim_type="statistical",
        scope="internal",
        domain="financial",
        requires=requires,
    )
    db_session.add(claim)
    await db_session.flush()
    return claim


def test_parse_money():
    assert parse_money("$112M") == 112_000_000
    assert parse_money("Revenue (current period) | $112M") == 112_000_000
    assert parse_money("$1.5B") == 1_500_000_000
    assert parse_money("no number here") is None


def test_parse_stated_percent():
    assert parse_stated_percent("Revenue grew 12% year-over-year") == 12.0
    assert parse_stated_percent("no percentage here") is None


def test_extract_current_prior():
    table_text = "Metric | Value\nRevenue (current period) | $112M\nRevenue (prior period) | $100M"
    current, prior = extract_current_prior(table_text)
    assert current == 112_000_000
    assert prior == 100_000_000


def test_resolves_deterministically_true_for_financial_statistical():
    assert resolves_deterministically("financial", "statistical", _registry()) is True


def test_resolves_deterministically_false_for_financial_causal():
    assert resolves_deterministically("financial", "causal", _registry()) is False


async def test_example_a_revenue_claim_is_supported(db_session):
    claim = await _make_claim(
        db_session,
        table_data=[["Metric", "Value"], ["Revenue (current period)", "$112M"], ["Revenue (prior period)", "$100M"]],
        claim_text="Revenue grew 12% year-over-year",
        requires=["current period revenue", "prior period revenue"],
    )

    result = await verify_deterministic(db_session, claim, _thresholds())

    assert result["final_verdict"] == "supported"
    assert result["resolved_by"] == "deterministic"
    assert result["computed_pct"] == 12.0


async def test_mismatched_figures_are_contradicted(db_session):
    claim = await _make_claim(
        db_session,
        table_data=[["Metric", "Value"], ["Revenue (current period)", "$112M"], ["Revenue (prior period)", "$100M"]],
        claim_text="Revenue grew 40% year-over-year",
        requires=["current period revenue", "prior period revenue"],
    )

    result = await verify_deterministic(db_session, claim, _thresholds())

    assert result["final_verdict"] == "contradicted"
    assert result["resolved_by"] == "deterministic"
    assert result["computed_pct"] == 12.0
    assert result["stated_pct"] == 40.0


async def test_missing_evidence_is_insufficient_not_a_guess(db_session):
    claim = await _make_claim(
        db_session,
        table_data=[["Metric", "Value"], ["Headcount", "500"]],
        claim_text="Revenue grew 12% year-over-year",
        requires=["current period revenue", "prior period revenue"],
    )

    result = await verify_deterministic(db_session, claim, _thresholds())

    assert result["final_verdict"] == "insufficient"
    assert result["resolved_by"] == "deterministic"
