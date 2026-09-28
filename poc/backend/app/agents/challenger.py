import json
import logging
import truststore
from dotenv import load_dotenv

from agno.agent import Agent
from agno.tools.serper import SerperTools
from agno.tools.exa import ExaTools

from app.agents.tools.citation_check import check_citation_fidelity_tool
from app.agents.tools.internal_lookup import make_internal_lookup_tool
from app.agents.verifier import format_evidence_bundle
from app.llm.client import build_model, load_prompt, require_llm_credentials
from app.schemas.verification import ChallengerResult, VerifierResult

logger = logging.getLogger(__name__)

truststore.inject_into_ssl()
load_dotenv()

_INSTRUCTIONS = load_prompt("challenger")


def build_challenger_agent(session, claim) -> Agent:
    require_llm_credentials()
    tools = [check_citation_fidelity_tool, ExaTools(
           show_results=True, 
           text_length_limit=1000
        )]
    if session is not None:
        tools.append(make_internal_lookup_tool(session, claim))
    return Agent(model=build_model("standard"), output_schema=ChallengerResult, tools=tools, markdown=False,compress_tool_results=True)


async def run_challenger(
    session, claim, evidence: list, verifier_result: VerifierResult
) -> tuple[ChallengerResult, str, str, list[dict]]:
    """Returns (result, prompt_sent, raw_response, tool_calls) — Phase 6.5 traces all four."""
    prompt = _INSTRUCTIONS.format(
        claim_text=claim.claim_text,
        evidence_bundle=format_evidence_bundle(evidence),
        verifier_verdict=verifier_result.verdict,
        verifier_confidence=verifier_result.confidence,
        verifier_reasoning=verifier_result.reasoning,
    )
    agent = build_challenger_agent(session, claim)
    response = await agent.arun(prompt)
    tool_calls = [
        {"tool_name": t.tool_name, "tool_args": t.tool_args, "result": str(t.result)} for t in (response.tools or [])
    ]

    raw_response = None
    parsed_result: ChallengerResult | None = None

    content = response.content
    if isinstance(content, str):
        raw_response = content
        try:
            parsed_json = json.loads(content)
            parsed_result = ChallengerResult.parse_obj(parsed_json)
        except Exception:
            logger.warning("Challenger agent returned free text; falling back to minimal ChallengerResult", exc_info=True)
            fallback = {
                "citation_fidelity": {"ok": False, "reasoning": content[:1000]},
                "basis_match": {"ok": False, "reasoning": content[:1000]},
                "completeness": {"ok": False, "reasoning": content[:1000]},
                "source_quality": {"ok": False, "reasoning": content[:1000]},
                "verdict": "insufficient",
                "accepted_verdict": "insufficient",
                "confidence": 0.0,
            }
            parsed_result = ChallengerResult.parse_obj(fallback)
    else:
        try:
            raw_response = content.model_dump_json()
        except Exception:
            try:
                raw_response = json.dumps(content)
            except Exception:
                raw_response = str(content)
        try:
            parsed_result = content
        except Exception:
            try:
                parsed_result = ChallengerResult.parse_raw(raw_response)
            except Exception:
                fallback = {
                    "citation_fidelity": {"ok": False, "reasoning": raw_response[:1000]},
                    "basis_match": {"ok": False, "reasoning": raw_response[:1000]},
                    "completeness": {"ok": False, "reasoning": raw_response[:1000]},
                    "source_quality": {"ok": False, "reasoning": raw_response[:1000]},
                    "verdict": "insufficient",
                    "accepted_verdict": "insufficient",
                    "confidence": 0.0,
                }
                parsed_result = ChallengerResult.parse_obj(fallback)

    return parsed_result, prompt, raw_response, tool_calls
