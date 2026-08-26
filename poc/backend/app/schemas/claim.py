from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ClaimType = Literal["statistical", "causal", "comparative", "definitional", "forward_looking", "hedged", "opinion"]
Scope = Literal["internal", "external", "both"]

class ClaimEntity(BaseModel):
    text: str = Field(..., description="The text of the entity.")
    label: str = Field(..., description="The label of the entity (spaCy-style: MONEY, PERCENT, DATE, ORG, LAW, GPE, etc).")

class ExtractedClaim(BaseModel):
    claim_id: str
    text: str = Field(..., description="Atomic, self-contained factual claim.")
    entities: List[ClaimEntity] = Field(default_factory=list)
    scope: Scope = Field(..., description="Whether the claim is about the document's own content (internal) or an outside source (external).")
    claim_type: ClaimType = Field(..., description="The type of claim (statistical, causal, comparative, definitional, forward-looking, hedged, opinion).")
    source_span: str = Field(..., description="Page/paragraph/section locator in the source document.")
    source_chunk_range: Optional[List[int]] = Field(
        default=None,
        description=(
            "(min_chunk_index, max_chunk_index) of the extraction window this claim came from. "
            "Set by the orchestrator after extraction, not by the agent itself — used to exclude "
            "the claim's own source chunks when retrieving in-document evidence."
        ),
    )
    requires: list[str] = Field(default_factory=list)
    cites_external_source: bool = Field(
        default=False, description="True if the claim text itself attributes the fact to an outside source."
    )
    is_opinion_or_unverifiable: bool = Field(
        default=False, description="True if this is a subjective/vague statement with no checkable fact."
    )


class ClaimList(BaseModel):
    claims: list[ExtractedClaim]


class RoutingDecision(BaseModel):
    claim_id: str
    route: Literal["internal", "external", "both", "unverifiable"]
    reasoning: str = Field(..., description="One or two sentences justifying the route — this is an audit field.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    suggested_search_queries: List[str] = Field(
        default_factory=list, description="Only populate if route is 'external' or 'both'."
    )

class InDocEvidence(BaseModel):
    claim_id: str
    verdict: Literal["supports", "contradicts", "not_enough_info"]
    matched_span: Optional[str] = Field(None, description="Quoted-in-your-own-words summary of the matching passage.")
    matched_location: Optional[str] = Field(None, description="Page/paragraph locator of the matching passage.")
    reasoning: str


class SourceCandidate(BaseModel):
    url: str
    title: str
    snippet: str


class SourceCredibilityScore(BaseModel):
    url: str
    tier: Literal["primary", "reputable_secondary", "aggregator", "low_quality"]
    score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., description="One or two sentences explaining the credibility score and tier assignment.")


class ExternalEvidence(BaseModel):
    claim_id: str
    verdict: Literal["supports", "contradicts", "not_enough_info"]
    source_url: str
    source_tier: Literal["primary", "reputable_secondary", "aggregator", "low_quality"]
    extracted_fact: str = Field(..., description="The specific fact pulled from the source, in your own words.")
    reasoning: str = Field(..., description="One or two sentences explaining how the source fact supports/contradicts the claim.")


class ClaimVerdict(BaseModel):
    claim_id: str
    claim_text: str
    final_verdict: Literal["supported", "contradicted", "partially_supported", "unverifiable"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_summary: str = Field(..., description="A brief summary of the evidence leading to the final verdict.")
    citations: List[str] = Field(..., description="Locators/URLs backing the verdict — the defensibility trail.")
    flagged_for_human_review: bool = Field(
        default=False, description="True for low-confidence, contradicted, or high-stakes (financial/legal) claims."
    )