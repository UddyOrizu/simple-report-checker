from typing import Literal

from pydantic import BaseModel

ClaimType = Literal["statistical", "causal", "comparative", "definitional", "forward_looking", "hedged", "opinion"]
Scope = Literal["internal", "external", "both"]


class ExtractedClaim(BaseModel):
    text: str
    claim_type: ClaimType
    scope: Scope
    source_span: str
    requires: list[str]


class ClaimList(BaseModel):
    claims: list[ExtractedClaim]
