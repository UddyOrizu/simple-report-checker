from unittest.mock import AsyncMock, patch

from app.agents.verify_claim import _gather_internal_evidence

_MODULE = "app.agents.verify_claim"


def _patch_ladder(vectorless, exact, semantic, cross_ref):
    return (
        patch(f"{_MODULE}.find_section_evidence", new=AsyncMock(return_value=vectorless)),
        patch(f"{_MODULE}.lookup_internal_evidence", new=AsyncMock(return_value=exact)),
        patch(f"{_MODULE}.semantic_internal_lookup", new=AsyncMock(return_value=semantic)),
        patch(f"{_MODULE}.resolve_cross_reference", new=AsyncMock(return_value=cross_ref)),
    )


async def test_vectorless_runs_first_and_short_circuits_the_rest():
    p_vec, p_exact, p_sem, p_xref = _patch_ladder(["vectorless"], ["exact"], ["semantic"], "xref")
    with p_vec as vec, p_exact as exact, p_sem as sem, p_xref as xref:
        evidence = await _gather_internal_evidence(session=None, claim=None)

    assert evidence == ["vectorless"]
    vec.assert_awaited_once()
    exact.assert_not_awaited()
    sem.assert_not_awaited()
    xref.assert_not_awaited()


async def test_falls_through_to_cheaper_lookups_when_vectorless_finds_nothing():
    p_vec, p_exact, p_sem, p_xref = _patch_ladder([], ["exact"], ["semantic"], "xref")
    with p_vec as vec, p_exact as exact, p_sem as sem, p_xref as xref:
        evidence = await _gather_internal_evidence(session=None, claim=None)

    assert evidence == ["exact"]
    vec.assert_awaited_once()
    exact.assert_awaited_once()
    sem.assert_not_awaited()
    xref.assert_not_awaited()


async def test_cross_reference_is_last_resort():
    p_vec, p_exact, p_sem, p_xref = _patch_ladder([], [], [], "xref")
    with p_vec, p_exact, p_sem, p_xref:
        evidence = await _gather_internal_evidence(session=None, claim=None)

    assert evidence == ["xref"]
