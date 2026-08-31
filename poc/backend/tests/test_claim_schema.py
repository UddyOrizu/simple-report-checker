import pytest
from pydantic import ValidationError

from app.schemas.claim import ClaimList, ExtractedClaim


def test_valid_claim_round_trips():
    claim = ExtractedClaim(
        text="Revenue grew 12% year-over-year",
        claim_type="statistical",
        source_span="Revenue grew 12% YoY",
        requires=["current period revenue", "prior period revenue"],
    )
    assert claim.claim_type == "statistical"


def test_invalid_claim_type_rejected():
    with pytest.raises(ValidationError):
        ExtractedClaim(text="x", claim_type="not_a_real_type", source_span="x", requires=[])


def test_claim_list_wraps_multiple_claims():
    claims = ClaimList(
        claims=[
            ExtractedClaim(text="a", claim_type="statistical", source_span="a", requires=[]),
            ExtractedClaim(text="b", claim_type="causal", source_span="b", requires=[]),
        ]
    )
    assert len(claims.claims) == 2
