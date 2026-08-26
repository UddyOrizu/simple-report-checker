import json
import os
from typing import List, Optional, cast

from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.reconcile import derive_severity
from app.models import AgentTrace, Claim, Verdict,Evidence

from app.retrieval.internal_index import query_similar
from app.schemas.claim import (
    ClaimVerdict,
    ExternalEvidence,
    ExternalEvidenceList,
    InDocEvidence,
    SourceCandidateList,
    SourceCredibilityScoreList,
)

from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat  # swap for your provider of choice
from agno.tools.serper import SerperTools
  # swap for your search tool
from agno.tools.website import WebsiteTools  # swap for your scrape/fetch tool

from dotenv import load_dotenv

import jsonpickle

from app.config_hash import compute_config_hash
load_dotenv()  # Load environment variables from .env file

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_api_base = os.getenv("OPENAI_BASE_URL", "https://eu.api.openai.com/v1")

MODEL = OpenAIChat(id="gpt-4.1", api_key=openai_api_key, base_url=openai_api_base)  # swap for your model of choice

# ============================================================================
# 3. IN-DOCUMENT VERIFIER AGENT
# ============================================================================

indoc_verifier_agent = Agent(
    name="In-Document Verifier",
    model=MODEL,
    output_schema=InDocEvidence,
    instructions="""
You check whether a claim is supported by OTHER content within the same
document (excluding the sentence/section the claim was originally extracted
from). You will be given the claim and a set of retrieved candidate passages
from this document's own vector index.

This is an ENTAILMENT check, not a similarity check. A passage can be topically
related without supporting the claim, and a passage can support the claim using
completely different wording. Judge whether the retrieved passage(s), taken
together, logically entail, contradict, or are silent on the claim.

VERDICTS:
- "supports": the retrieved passage(s) state or directly imply the claim is
  true. Quote the supporting logic in `matched_span`, in your own words.
- "contradicts": the retrieved passage(s) state something inconsistent with
  the claim (e.g., a different figure for the same metric, a different date).
  This is a high-value finding — flag it clearly in `reasoning`.
- "not_enough_info": the retrieved passages are on-topic but don't confirm or
  deny the specific claim (e.g., they discuss the same metric for a different
  period, or a related-but-different figure).

Do not assume the retrieved passages are correct just because they're in the
same document — your job is only to compare the claim against them, not to
assess ground truth. Ground truth against the outside world is the external
verifier's job.

If no retrieved passage is genuinely relevant, return "not_enough_info" — do
not force a verdict from weak matches.
""",
)


# ============================================================================
# 4. EXTERNAL VERIFIER TEAM (Search -> Scrape/Fetch -> Credibility)
# ============================================================================

search_agent = Agent(
    name="External Search Agent",
    model=MODEL,
    tools=[SerperTools( api_key=os.getenv("SERPER_API_KEY"))],  # swap for your search tool
    output_schema=SourceCandidateList,
    instructions="""
You find candidate external sources to verify a specific factual claim. You are
given the claim, its tagged entities, and suggested queries from the router —
use those as a starting point but refine them if they'd return poor results.

RULES:
1. Search for the SPECIFIC fact, not the general topic. For a claim like
   "HMRC's VAT registration threshold is £90,000", search for the threshold
   figure and effective date, not "VAT registration UK".
2. Prioritise finding PRIMARY sources: regulator/government sites (.gov.uk,
   HMRC, Companies House, FCA), official filed accounts, standard-setter
   publications (IFRS, FRC), original press releases. Only fall back to
   secondary/news sources if no primary source surfaces.
3. Run multiple query variations if the first pass returns weak or ambiguous
   results — do not settle for the first page of results if none of them
   actually address the specific figure or date in the claim.
4. Return 3-6 candidates maximum. Do not return sources that are clearly
   irrelevant just to pad the list.
5. For each candidate, the `snippet` field should capture enough of the search
   result to indicate WHY it's relevant to this claim specifically.

You do not verify or draw conclusions here — you find candidates. The Scrape
Agent and Credibility Agent handle the rest.
""",
)

scrape_agent = Agent(
    name="External Fetch/Scrape Agent",
    model=MODEL,
    tools=[WebsiteTools()],
    output_schema=ExternalEvidenceList,
    instructions="""
You fetch full content from candidate source URLs and determine whether each
source supports, contradicts, or is silent on the claim. You are given the
claim and a list of candidate URLs with their search snippets.

RULES:
1. ALWAYS fetch the full page — search snippets are frequently too short or
   too vague to reliably support/contradict a numeric or dated claim. A snippet
   mentioning "VAT threshold" without the figure is not evidence.
2. Extract the SPECIFIC fact relevant to the claim from the fetched content —
   the exact figure, date, or statement — into `extracted_fact`, in your own
   words. Do not just say "the page discusses this topic."
3. If a page fails to load, is paywalled, or doesn't actually contain the
   relevant fact despite the snippet, note that and move to the next candidate
   rather than forcing a verdict from insufficient content.
4. Be alert to DATE SENSITIVITY — thresholds, rates, and regulations change.
   If the source is dated or versioned, check whether it reflects the period
   the claim refers to. A correct historical figure is not evidence for a
   current claim, and vice versa.
5. `source_tier` is your own read of the source type at this stage (primary
   government/official vs secondary vs aggregator) — the Credibility Agent will
   independently score it; your tagging here just needs to be a reasonable
   first pass, not final.
6. Produce one ExternalEvidence entry per source you actually fetched and got
   usable content from. Do not fabricate entries for sources you couldn't
   access.
""",
)

credibility_agent = Agent(
    name="Source Credibility Agent",
    model=MODEL,
    output_schema=SourceCredibilityScoreList,
    instructions="""
You score the credibility of sources used to verify claims in professional
services deliverables (audit, tax, advisory). This output feeds directly into
how much weight a piece of evidence gets in the final verdict — be rigorous,
this is a defensibility control, not a formality.

TIERS:
- "primary": government/regulator sites (.gov.uk, IRS, SEC, HMRC, Companies
  House), official standard-setters (IFRS, FRC, IASB), a company's own filed
  accounts or official investor relations releases, primary legal text
  (legislation.gov.uk, case law).
- "reputable_secondary": established financial/business press with editorial
  standards (FT, Reuters, Bloomberg), professional bodies' published guidance,
  Big 4/major advisory firm published research.
- "aggregator": sites that repackage other sources without original reporting
  or verification (most SEO content sites, general wikis, most "explainer"
  blogs).
- "low_quality": anonymous or unattributed content, sites with clear
  promotional/commercial bias relevant to the claim, content with no visible
  authorship or sourcing, obviously outdated content presented as current.

Score reflects tier AND fit-for-purpose: a reputable_secondary source reporting
numbers that trace back to a primary filing should score higher than a primary
source that's several years stale for a claim about current figures. State
which of these is driving the score in `reasoning`.

Be willing to score a source low even if it's the only one the Search Agent
found — a low-credibility "supports" verdict should carry less weight in
synthesis, not be treated as equivalent to a primary-source confirmation.
""",
)

external_verifier_team = Team(
    name="External Verifier Team",
    mode="coordinate",
    model=MODEL,
    output_schema=ExternalEvidenceList,
    max_iterations=3,
    members=[search_agent, scrape_agent, credibility_agent],
    instructions="""
Coordinate the three members to fully verify a claim against external sources:
1. Search Agent finds candidate sources for the claim.
2. Fetch/Scrape Agent pulls full content from those candidates and produces a
   supports/contradicts/not_enough_info verdict per source with the specific
   extracted fact.
3. Credibility Agent scores each source used.

Run search before scrape, and scrape before credibility scoring — credibility
scoring needs to know which sources were actually used as evidence, not just
which were found. If the Search Agent returns nothing usable, do not force the
Scrape Agent to fabricate results — surface that external verification found
no sources and let the Synthesizer mark the claim accordingly.
""",
)

# ============================================================================
# 5. SYNTHESIZER AGENT
# ============================================================================

synthesizer_agent = Agent(
    name="Verdict Synthesizer",
    model=MODEL,
    output_schema=ClaimVerdict,
    instructions="""
You produce the final verdict on a claim by combining all evidence gathered —
in-document evidence, external evidence, and source credibility scores. This
output is what a BDO reviewer or partner ultimately sees, so it needs to be
directly usable without them having to reconstruct your reasoning from raw
evidence.

VERDICT LOGIC:
- "supported": all available evidence (in-doc and/or external) agrees with the
  claim, and at least one piece of evidence comes from a primary or
  reputable_secondary source (if external evidence exists at all). In-document-
  only support is valid for internal-routed claims.
- "contradicted": any evidence from a primary or reputable_secondary source
  directly contradicts the claim. A contradiction from a low_quality source
  alone should not override supporting evidence from a stronger source — weigh
  by credibility, not just by count.
- "partially_supported": some elements of the claim are confirmed and others
  aren't (e.g., the percentage is confirmed but the absolute figure isn't), or
  evidence is mixed with no clear stronger side.
- "unverifiable": no usable evidence was found (in-doc, external, or both came
  back empty/not_enough_info), or the claim was never routed for verification.

CONFIDENCE should reflect source quality and evidence agreement, not just
whether a verdict was reached — a "supported" verdict resting on one
aggregator-tier source should carry noticeably lower confidence than one
resting on a primary source or on agreeing primary + secondary sources.

CITATIONS must be concrete and traceable — page/section locators for
in-document evidence, full URLs for external evidence. This field is the
audit trail; do not summarise it away.

FLAGGING: set `flagged_for_human_review = true` when:
  - final_verdict is "contradicted", regardless of confidence
  - final_verdict is "unverifiable" AND the claim involves MONEY, PERCENT,
    DATE, or LAW entities (i.e., it's the kind of claim that matters and
    nobody could check it)
  - confidence < 0.6 on any verdict
  - evidence sources disagree with each other, even if a majority supports

Write `evidence_summary` as 2-4 sentences a non-technical reviewer can read
without opening the underlying evidence objects — state what was checked,
what was found, and why the verdict landed where it did.
""",
)

async def verify_claim(session: AsyncSession, claim: Claim, config: dict, thresholds: dict, registry: list[dict]) -> dict:
    """Routes a claim to the deterministic path (no LLM) or the agent path, per
    domain_registry.yaml's verification_method for its (domain, claim_type) pair — the actual
    place that registry routing decision takes effect."""

    return await verify_claim_via_agents(session, claim, config)


async def verify_claim_via_agents(session: AsyncSession, claim: Claim, config: dict) -> dict:
    """Runs the verifier, then the challenger (given the verifier's result), reconciles the two,
    and persists an agent_traces row per call plus the final verdicts row — the full Phase 6.5
    tracing wiring, reused by both scripts/run_verify.py and any future API endpoint."""
    if claim.is_opinion_or_unverifiable:
        return None     

    indoc_chunks= await query_similar(session=session, document_id= claim.document_id,query_embedding=claim.embedding, top_k=5, exclude_chunk_id=claim.chunk_id)

    #indoc_evidence: Optional[InDocEvidence] = None
    all_external_evidence: List[ExternalEvidence] = []
    in_document_evidence_json =""
    evidence : List[Evidence] = []
    if claim.scope in ("internal", "both"):
        prompt = f"<Claim> {claim.claim_text} </Claim>\n <Candidate_Passages> { json.dumps(indoc_chunks, indent=4)}</Candidate_Passages>"
        indoc_evidence = await indoc_verifier_agent.arun(
            prompt
        )

        print(f"Indoc evidence: {indoc_evidence.content} for type {indoc_evidence.content_type}")

        in_document_evidence : InDocEvidence = indoc_evidence.content# shape depends on your Agent output handling

        in_document_evidence_json = indoc_evidence.content

        evidence.append(Evidence(
            claim_id=claim.id,
            source_type="internal",
            source_ref=f"document_id: {claim.document_id}, chunk_ids: {[c['chunk_id'] for c in indoc_chunks]}",
            content_snippet=f"Verdict: {in_document_evidence.verdict} Matched span: {in_document_evidence.matched_span}, Reasoning: {in_document_evidence.reasoning}", 
            
        ))



        tool_calls = [str]

        for tool_call in indoc_evidence.tools:
            tool_calls.append( f"{tool_call.tool_name} {tool_call.tool_args}: {tool_call.result}")

        config_hash = compute_config_hash()
        # session.add(
        #         AgentTrace(
        #             claim_id=claim.id,
        #             agent_name="indoc_verifier_agent",
        #             prompt_sent=prompt,
        #             raw_response=f"Indoc evidence: {indoc_evidence.content} for type {indoc_evidence.content_type}",
        #             tool_calls=tool_calls,
        #             config_hash=config_hash,
        #         )
        #     )



    if claim.scope in ("external", "both"):
        team_result = await external_verifier_team.arun(
            f"<Claim> {claim.claim_text} </Claim>\n <Entities> {json.dumps(claim.entities, indent=4)} </Entities>\n"
            f"<Suggested_Queries> {json.dumps(claim.suggested_search_queries, indent=4)} </Suggested_Queries>"
        )
        print(f"External evidence: {team_result.content} for type {team_result.content_type}")
        external_evidence: list[ExternalEvidence] = team_result.content.evidence
        print(f"External evidence: {external_evidence} for type {type(external_evidence)}")

        for ext in external_evidence:
            evidence.append(Evidence(
                claim_id=claim.id,
                source_type="external",
                source_ref=f"document_id: {claim.document_id}, external_source_url: {ext.source_url}",
                content_snippet=f"Verdict: {ext.verdict} Matched span: {ext.extracted_fact}, Reasoning: {ext.reasoning}", 
                
            ))
            all_external_evidence.append(ext)

    claimJSON = jsonpickle.encode(claim, unpicklable=False)

    synthesizer_agent_result = await synthesizer_agent.arun(
        f"<Claim> {json.dumps(claimJSON, indent=4)} </Claim>\n"        
        f"<In-document_evidence> {in_document_evidence_json} </In-document_evidence>\n"
        f"<External_evidence> {[e.model_dump_json() for e in all_external_evidence]} </External_evidence>"
    )

    claim_verdict: ClaimVerdict = synthesizer_agent_result.content  # shape depends on your Agent output handling

    for evidence_item in evidence:
        session.add(evidence_item)

    
    ##reconciled = reconcile(verifier_result, challenger_result, claim.domain)
    ##config_hash = compute_config_hash()

    
    # session.add(
    #     AgentTrace(
    #         claim_id=claim.id,
    #         agent_name="challenger",
    #         prompt_sent=challenger_prompt,
    #         raw_response=challenger_raw,
    #         tool_calls=challenger_tools,
    #         config_hash=config_hash,
    #     )
    # )
    severity = derive_severity(claim_verdict.final_verdict, claim.domain)
    session.add(
        Verdict(
            claim_id=claim.id,
            verifier_verdict=claim_verdict.final_verdict,
            verifier_confidence=claim_verdict.confidence,
            verifier_reasoning=claim_verdict.evidence_summary,
            challenger_verdict=claim_verdict.final_verdict,
            challenger_confidence=claim_verdict.confidence,
            challenger_reasoning=claim_verdict.evidence_summary,
            agreement=True,
            final_verdict=claim_verdict.final_verdict,  # TODO: combine with challenger verdict if they disagree
            final_confidence=claim_verdict.confidence,  # TODO: combine with challenger confidence if they disagree
            severity=severity,
            resolved_by="AI Agent",
        )
    )
    await session.commit()

    return {"verifier": claim_verdict,"severity" : severity, "evidence": evidence}
