import os
from functools import lru_cache
from typing import Literal

from agno.agent import Agent
from agno.models.anthropic import Claude
from agno.models.openai import OpenAIChat
from pydantic import BaseModel

import truststore
from dotenv import load_dotenv

load_dotenv()

Tier = Literal["standard", "mini"]

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts")

# Which LLM backend every agent in this pipeline talks to — one switch for the whole backend
# rather than a per-file choice, so a mixed-provider pipeline (each agent needing its own
# credential check and tracing) doesn't have to exist. "standard" is each provider's strong
# model (decomposition, verification, challenging); "mini" is the cheap/fast tier the navigator
# uses for a much simpler section-picking task. Any of the four model IDs can be overridden
# independently via env var without touching code.
LLM_PROVIDER: Literal["openai", "anthropic"] = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()  # type: ignore[assignment]

_OPENAI_MODEL_IDS: dict[Tier, str] = {
    "standard": os.getenv("OPENAI_MODEL_ID", "gpt-4o"),
    "mini": os.getenv("OPENAI_MINI_MODEL_ID", "gpt-4o-mini"),
}
_ANTHROPIC_MODEL_IDS: dict[Tier, str] = {
    "standard": os.getenv("ANTHROPIC_MODEL_ID", "claude-sonnet-4-5-20250929"),
    "mini": os.getenv("ANTHROPIC_MINI_MODEL_ID", "claude-haiku-4-5-20251001"),
}
_CREDENTIAL_ENV_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class MissingCredentialsError(RuntimeError):
    """Raised when an LLM-backed pipeline stage runs without the active provider's API key set
    (ANTHROPIC_API_KEY or OPENAI_API_KEY, depending on LLM_PROVIDER).

    Deterministic stages (ingestion, chunking, structural indexing, arithmetic verification)
    never raise this — only stages that actually need a model call.
    """


def has_llm_credentials() -> bool:
    """Whichever provider LLM_PROVIDER selects, is its API key actually set? Used by tests to
    decide whether to skip LLM-dependent cases instead of hardcoding a check against one
    provider's env var."""
    env_var = _CREDENTIAL_ENV_VARS.get(LLM_PROVIDER)
    return bool(env_var and os.environ.get(env_var))


def require_llm_credentials() -> None:
    """Called at actual call time — never at the agents' eager import-time construction, several
    of which build a module-level Agent/model as soon as the file is imported — so a missing key
    surfaces as this clean, named error rather than an opaque network/auth failure three layers
    deep inside agno, or worse, an import-time crash that would take down stages that don't need
    an LLM at all."""
    if LLM_PROVIDER not in _CREDENTIAL_ENV_VARS:
        raise ValueError(f"Unknown LLM_PROVIDER {LLM_PROVIDER!r} — expected 'openai' or 'anthropic'")
    env_var = _CREDENTIAL_ENV_VARS[LLM_PROVIDER]
    if not os.environ.get(env_var):
        raise MissingCredentialsError(f"{env_var} is not set — this pipeline stage is BLOCKED-CREDENTIALS")


def build_model(tier: Tier = "standard"):
    """Builds the Agno model object for whichever provider LLM_PROVIDER selects — the one place
    every agent in this pipeline (verifier, challenger, decomposer, navigator, router, the
    external evidence team) gets its model from, so switching providers is one env var instead
    of an edit per agent file. Doesn't validate credentials itself — callers that construct a
    model eagerly at import time need that deferred to require_llm_credentials() at call time;
    callers that only ever build lazily (like _agent() below) can call both back to back."""
    if LLM_PROVIDER == "anthropic":
        return Claude(id=_ANTHROPIC_MODEL_IDS[tier], api_key=os.getenv("ANTHROPIC_API_KEY"))
    if LLM_PROVIDER == "openai":
        return OpenAIChat(
            id=_OPENAI_MODEL_IDS[tier],
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://eu.api.openai.com/v1"),
        )
    raise ValueError(f"Unknown LLM_PROVIDER {LLM_PROVIDER!r} — expected 'openai' or 'anthropic'")


@lru_cache(maxsize=None)
def _agent(tier: Tier, output_schema: type[BaseModel] | None = None) -> Agent:
    require_llm_credentials()
    return Agent(model=build_model(tier), markdown=False, output_schema=output_schema)


async def llm_call(prompt: str, tier: Tier = "standard") -> str:
    response = await _agent(tier).arun(prompt)
    return response.content


async def llm_call_structured(prompt: str, output_schema: type[BaseModel], tier: Tier = "standard"):
    """Like llm_call, but returns a parsed instance of `output_schema` instead of raw text."""
    response = await _agent(tier, output_schema).arun(prompt)
    return response.content


def load_prompt(name: str) -> str:
    """Load a prompt template from backend/prompts/ by name, e.g. "section_summarizer" or
    "section_summarizer.md" — resolved by file location, independent of the caller's cwd."""
    filename = name if name.endswith(".md") else f"{name}.md"
    path = os.path.join(PROMPTS_DIR, os.path.basename(filename))
    with open(path) as f:
        return f.read()
