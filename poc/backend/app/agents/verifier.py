import json
import logging
import truststore
from dotenv import load_dotenv

from agno.agent import Agent
from agno.tools.serper import SerperTools
from agno.tools.exa import ExaTools

from app.agents.tools.internal_lookup import make_internal_lookup_tool
from app.llm.client import build_model, load_prompt, require_llm_credentials
from app.schemas.verification import VerifierResult

logger = logging.getLogger(__name__)

truststore.inject_into_ssl()
load_dotenv()

_INSTRUCTIONS = load_prompt("verifier")


def build_verifier_agent(session, claim) -> Agent:
    """A fresh Agent per call, not the shared cached client — its internal_lookup tool is bound
    to this specific session+claim via closure, so it can't be reused across calls the way the
    plain structured-output agents (decomposer, navigator) are."""
    require_llm_credentials()
    tools = [make_internal_lookup_tool(session, claim),ExaTools(
            show_results=True, 
            text_length_limit=1000
        )] if session is not None else []
    return Agent(model=build_model("standard"), output_schema=VerifierResult, tools=tools, markdown=False, compress_tool_results=True)


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
    raw_response = None
    parsed_result: VerifierResult | None = None

    # response.content may be a pydantic model instance (with model_dump_json)
    # or a plain string (agent returned unstructured text). Handle both.
    content = response.content
    if isinstance(content, str):
        raw_response = content
        # try to parse JSON into VerifierResult
        try:
            parsed_json = json.loads(content)
            parsed_result = VerifierResult.parse_obj(parsed_json)
        except Exception:
            logger.warning("Verifier agent returned free text; falling back to minimal VerifierResult", exc_info=True)
            parsed_result = VerifierResult(verdict="insufficient", confidence=0.0, reasoning=content[:1000], citations=[])
    else:
        # assume a pydantic/BaseModel-like object
        try:
            raw_response = content.model_dump_json()
        except Exception:
            try:
                raw_response = json.dumps(content)
            except Exception:
                raw_response = str(content)
        # keep the parsed model if possible
        try:
            parsed_result = content
        except Exception:
            # last resort: try to coerce into VerifierResult
            try:
                parsed_result = VerifierResult.parse_raw(raw_response)
            except Exception:
                parsed_result = VerifierResult(verdict="insufficient", confidence=0.0, reasoning=raw_response[:1000], citations=[])

    return parsed_result, prompt, raw_response, tool_calls
