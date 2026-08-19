import os

import truststore
from dotenv import load_dotenv

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.serper import SerperTools

from app.agents.tools.internal_lookup import make_internal_lookup_tool
from app.llm.client import MissingCredentialsError, load_prompt
from app.schemas.verification import VerifierResult

truststore.inject_into_ssl()
load_dotenv()

VERIFIER_MODEL_ID = "gpt-4o"
_INSTRUCTIONS = load_prompt("verifier")


def build_verifier_agent(session, claim) -> Agent:
    """A fresh Agent per call, not the shared cached client — its internal_lookup tool is bound
    to this specific session+claim via closure, so it can't be reused across calls the way the
    plain structured-output agents (decomposer, navigator) are."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingCredentialsError("ANTHROPIC_API_KEY is not set — this pipeline stage is BLOCKED-CREDENTIALS")
    tools = [make_internal_lookup_tool(session, claim)] if session is not None else []
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://eu.api.openai.com/v1")
    return Agent(model=OpenAIChat(id=VERIFIER_MODEL_ID, api_key=api_key, base_url=base_url), output_schema=VerifierResult, tools=tools, markdown=False)


def format_evidence_bundle(evidence: list) -> str:
    if not evidence:
        return "(no evidence retrieved)"
    return "\n".join(f"- [{e.source_type}] {e.source_ref}: {e.content_snippet}" for e in evidence)


async def run_verifier(session, claim, evidence: list) -> tuple[VerifierResult, str, str, list[dict]]:
    """Returns (result, prompt_sent, raw_response, tool_calls) — Phase 6.5 traces all four."""
    prompt = _INSTRUCTIONS.format(
        claim_text=claim.claim_text,
        claim_type=claim.claim_type,
        scope=claim.scope,
        evidence_bundle=format_evidence_bundle(evidence),
    )
    agent = build_verifier_agent(session, claim)
    response = await agent.arun(prompt)
    tool_calls = [
        {"tool_name": t.tool_name, "tool_args": t.tool_args, "result": str(t.result)} for t in (response.tools or [])
    ]
    return response.content, prompt, response.content.model_dump_json(), tool_calls
