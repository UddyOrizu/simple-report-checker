import asyncio
import json
import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.challenger import run_challenger
from app.agents.deterministic_verifier import resolves_deterministically, verify_deterministic
from app.agents.reconcile import derive_severity, reconcile
from app.agents.verifier import run_verifier
from app.config_hash import compute_config_hash
from app.llm.client import MissingCredentialsError
from app.models import AgentTrace, Claim, Evidence, Verdict
from app.retrieval.cross_reference import resolve_cross_reference
from app.retrieval.internal_index import lookup_internal_evidence
from app.schemas.claim import ExternalEvidence, ExternalEvidenceList, SourceCandidateList, SourceCredibilityScoreList

from agno.agent import Agent
from agno.models.openai import OpenAIChat  # swap for your provider of choice
from agno.tools.serper import SerperTools  # swap for your search tool
from agno.tools.website import WebsiteTools  # swap for your scrape/fetch tool

from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_api_base = os.getenv("OPENAI_BASE_URL", "https://eu.api.openai.com/v1")

MODEL = OpenAIChat(id="gpt-4.1", api_key=openai_api_key, base_url=openai_api_base)  # swap for your model of choice

# One claim's external evidence gathering (search -> scrape -> credibility) shouldn't be able to
# stall an entire document's verification run if a fetched site hangs or a search API is slow.
EXTERNAL_VERIFICATION_TIMEOUT_SECONDS = 90

_SOURCE_TIER_AUTHORITY = {"primary": 1.0, "reputable_secondary": 0.75, "aggregator": 0.4, "low_quality": 0.15}
_UNSCORED_FALLBACK_TIER = "aggregator"

# ============================================================================
# EXTERNAL EVIDENCE PIPELINE (Search -> Scrape/Fetch -> Credibility)
#
# Three single-purpose agents, run explicitly in sequence by _run_external_pipeline below rather
# than handed to an LLM coordinator — a "coordinate"-mode team can decide on its own to skip a
# member it judges unnecessary, which silently drops exactly the credibility scoring step this
# pipeline exists to guarantee. This is evidence-gathering only — it hands its findings to the
# verifier/challenger pair further down for the actual verdict, the same two-agent adversarial
# pipeline used for in-document evidence, rather than reaching its own separate conclusion.
# ============================================================================

search_agent = Agent(
    name="External Search Agent",
    model=MODEL,
    tools=[SerperTools(api_key=os.getenv("SERPER_API_KEY"))],  # swap for your search tool
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
4. Populate `source_as_of_date` with the specific date/period/version the
   source's content reflects (e.g. "2024/25 tax year", "March 2025", "last
   updated Jan 2025") — thresholds, rates, and regulations change, and this is
   what lets a downstream check judge whether the source is actually current
   for the claim, rather than a correct-but-stale figure being mistaken for
   support. Leave it null only if the page genuinely gives no dating signal.
5. Produce one ExternalEvidence entry per source you actually fetched and got
   usable content from. Do not fabricate entries for sources you couldn't
   access. Do not guess at the source's credibility tier — that's the
   Credibility Agent's job, not yours; stay focused on what the page says.
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

def _require_openai_credentials() -> None:
    """The external team's agents are constructed eagerly at import time (agno doesn't validate
    credentials until a real call is made), so without this explicit check a missing
    OPENAI_API_KEY would surface as an opaque network/auth error deep inside Team.arun rather
    than the clean MissingCredentialsError every other LLM-dependent stage raises."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise MissingCredentialsError("OPENAI_API_KEY is not set — external verification is BLOCKED-CREDENTIALS")


async def _gather_internal_evidence(session: AsyncSession, claim: Claim) -> list[Evidence]:
    """Direct lookup first (5.1); only pay for the LLM-backed cross-reference navigator (5.1.1)
    when the direct lookup genuinely misses — the common case is evidence co-located with the
    claim, where a navigation call would be wasted cost."""
    evidence = await lookup_internal_evidence(session, claim)
    if evidence:
        return evidence
    cross_ref = await resolve_cross_reference(session, claim)
    return [cross_ref] if cross_ref is not None else []


async def _gather_external_evidence(claim: Claim) -> list[Evidence]:
    """Explicitly runs all three stages in sequence — search, then scrape, then credibility
    scoring — rather than delegating to an LLM coordinator that could decide on its own to skip
    a member. Each stage's output gates the next: no candidates means no scrape call, no fetched
    evidence means no credibility call, so every claim that reaches this function and finds
    something at all runs through all three agents, not a coordinator's discretionary subset."""
    _require_openai_credentials()
    try:
        return await asyncio.wait_for(_run_external_pipeline(claim), timeout=EXTERNAL_VERIFICATION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return []


async def _run_external_pipeline(claim: Claim) -> list[Evidence]:
    search_result = await search_agent.arun(
        f"<Claim> {claim.claim_text} </Claim>\n"
        f"<Entities> {json.dumps(claim.entities, indent=4)} </Entities>\n"
        f"<Suggested_Queries> {json.dumps(claim.suggested_search_queries, indent=4)} </Suggested_Queries>"
    )
    candidates = search_result.content.candidates
    if not candidates:
        return []

    scrape_result = await scrape_agent.arun(
        f"<Claim> {claim.claim_text} </Claim>\n<Candidate_Sources>\n"
        + "\n".join(f"- {c.url} — {c.title}: {c.snippet}" for c in candidates)
        + "\n</Candidate_Sources>"
    )
    fetched: list[ExternalEvidence] = scrape_result.content.evidence
    if not fetched:
        return []

    credibility_result = await credibility_agent.arun(
        f"<Claim> {claim.claim_text} </Claim>\n<Sources_To_Score>\n"
        + "\n".join(f"- {ext.source_url}" for ext in fetched)
        + "\n</Sources_To_Score>"
    )
    scores_by_url = {score.url: score for score in credibility_result.content.scores}

    evidence: list[Evidence] = []
    for ext in fetched:
        score = scores_by_url.get(ext.source_url)
        # The Credibility Agent scores every URL the Scrape Agent produced evidence for, so a
        # missing score should only happen if that agent's response was incomplete — fall back to
        # a conservative middle tier rather than trusting an un-scored source at face value.
        tier = score.tier if score else _UNSCORED_FALLBACK_TIER
        authority = score.score if score else _SOURCE_TIER_AUTHORITY[_UNSCORED_FALLBACK_TIER]
        credibility_note = f"Credibility: {score.reasoning}" if score else "Credibility: score unavailable, defaulted conservatively."
        date_note = f" As of: {ext.source_as_of_date}." if ext.source_as_of_date else ""
        evidence.append(
            Evidence(
                claim_id=claim.id,
                source_type="external",
                source_ref=f"{ext.source_url} (tier: {tier})",
                content_snippet=(
                    f"Verdict: {ext.verdict}. Extracted fact: {ext.extracted_fact}.{date_note} "
                    f"Reasoning: {ext.reasoning} {credibility_note}"
                ),
                authority_score=authority,
            )
        )
    return evidence


async def verify_claim(session: AsyncSession, claim: Claim, config: dict, thresholds: dict, registry: list[dict]) -> dict | None:
    """Routes a claim to the deterministic path (no LLM, exact arithmetic recompute) when
    domain_registry.yaml's verification_method says so for this (domain, claim_type) pair —
    every claim used to go through the agent path regardless. Everything else goes through the
    verifier/challenger agent pipeline. Returns None for claims flagged unverifiable/opinion at
    extraction — nothing to check."""
    if claim.is_opinion_or_unverifiable:
        return None

    if resolves_deterministically(claim.domain, claim.claim_type, registry):
        return await _verify_deterministic_and_persist(session, claim, thresholds)

    return await verify_claim_via_agents(session, claim, config)


async def _verify_deterministic_and_persist(session: AsyncSession, claim: Claim, thresholds: dict) -> dict:
    result = await verify_deterministic(session, claim, thresholds)
    evidence: list[Evidence] = result["evidence"]
    for item in evidence:
        session.add(item)

    if "reasoning" in result:
        reasoning = result["reasoning"]
    else:
        reasoning = (
            f"Recomputed {result['computed_pct']:.2f}% vs. stated {result['stated_pct']:.2f}% "
            f"(difference {result['difference']:.2f}pp)"
        )

    reconciled = {
        "final_verdict": result["final_verdict"],
        "final_confidence": 1.0,
        "agreement": None,
        "severity": derive_severity(result["final_verdict"], claim.domain),
        "resolved_by": "deterministic",
    }
    session.add(
        Verdict(
            claim_id=claim.id,
            verifier_verdict=result["final_verdict"],
            verifier_confidence=1.0,
            verifier_reasoning=reasoning,
            agreement=None,
            final_verdict=reconciled["final_verdict"],
            final_confidence=reconciled["final_confidence"],
            severity=reconciled["severity"],
            resolved_by="deterministic",
        )
    )
    await session.commit()
    return {"reconciled": reconciled, "evidence": evidence}


async def verify_claim_via_agents(session: AsyncSession, claim: Claim, config: dict) -> dict:
    """Gathers evidence from wherever claim.scope says to look (internal lookup + cross-reference
    fallback, and/or the real search->scrape->credibility external team — concurrently when scope
    is "both", not sequentially), then runs it through the verifier/challenger adversarial pair
    and reconciles their verdicts. Persists one AgentTrace row per agent and one verdicts row —
    the actual Phase 6.5 tracing contract, not the fabricated agreement data this used to write."""
    tasks = []
    if claim.scope in ("internal", "both"):
        tasks.append(_gather_internal_evidence(session, claim))
    if claim.scope in ("external", "both"):
        tasks.append(_gather_external_evidence(claim))

    evidence: list[Evidence] = []
    for result in await asyncio.gather(*tasks):
        evidence.extend(result)

    verifier_result, verifier_prompt, verifier_raw, verifier_tools = await run_verifier(session, claim, evidence)
    challenger_result, challenger_prompt, challenger_raw, challenger_tools = await run_challenger(
        session, claim, evidence, verifier_result
    )
    reconciled = reconcile(verifier_result, challenger_result, claim.domain)

    config_hash = compute_config_hash()
    for item in evidence:
        session.add(item)
    session.add(
        AgentTrace(
            claim_id=claim.id,
            agent_name="verifier",
            prompt_sent=verifier_prompt,
            raw_response=verifier_raw,
            tool_calls=verifier_tools,
            config_hash=config_hash,
        )
    )
    session.add(
        AgentTrace(
            claim_id=claim.id,
            agent_name="challenger",
            prompt_sent=challenger_prompt,
            raw_response=challenger_raw,
            tool_calls=challenger_tools,
            config_hash=config_hash,
        )
    )
    session.add(
        Verdict(
            claim_id=claim.id,
            verifier_verdict=verifier_result.verdict,
            verifier_confidence=verifier_result.confidence,
            verifier_reasoning=verifier_result.reasoning,
            challenger_verdict=challenger_result.verdict,
            challenger_confidence=challenger_result.confidence,
            challenger_reasoning=challenger_result.compose_reasoning(),
            agreement=reconciled["agreement"],
            final_verdict=reconciled["final_verdict"],
            final_confidence=reconciled["final_confidence"],
            severity=reconciled["severity"],
            resolved_by=reconciled["resolved_by"],
        )
    )
    await session.commit()

    return {"reconciled": reconciled, "evidence": evidence}
