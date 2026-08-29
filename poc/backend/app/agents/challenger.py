import truststore
from dotenv import load_dotenv

from agno.agent import Agent
from agno.tools.serper import SerperTools

from app.agents.tools.citation_check import check_citation_fidelity_tool
from app.agents.tools.internal_lookup import make_internal_lookup_tool
from app.agents.verifier import format_evidence_bundle
from app.llm.client import build_model, load_prompt, require_llm_credentials
from app.schemas.verification import ChallengerResult, VerifierResult

truststore.inject_into_ssl()
load_dotenv()

_INSTRUCTIONS = load_prompt("challenger")


def build_challenger_agent(session, claim) -> Agent:
    require_llm_credentials()
    tools = [check_citation_fidelity_tool, SerperTools()]
    if session is not None:
        tools.append(make_internal_lookup_tool(session, claim))
    return Agent(model=build_model("standard"), output_schema=ChallengerResult, tools=tools, markdown=False)


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
    return response.content, prompt, response.content.model_dump_json(), tool_calls
