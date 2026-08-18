import os
from functools import lru_cache

from agno.agent import Agent
from agno.models.anthropic import Claude
from pydantic import BaseModel

DEFAULT_MODEL_ID = "claude-haiku-4-5-20251001"

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts")


class MissingCredentialsError(RuntimeError):
    """Raised when an LLM-backed pipeline stage runs without ANTHROPIC_API_KEY set.

    Deterministic stages (ingestion, chunking, structural indexing, arithmetic verification)
    never raise this — only stages that actually need a model call.
    """


@lru_cache(maxsize=None)
def _agent(model_id: str, output_schema: type[BaseModel] | None = None) -> Agent:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingCredentialsError(
            "ANTHROPIC_API_KEY is not set — this pipeline stage is BLOCKED-CREDENTIALS"
        )
    return Agent(model=Claude(id=model_id), markdown=False, output_schema=output_schema)


async def llm_call(prompt: str, model_id: str = DEFAULT_MODEL_ID) -> str:
    response = await _agent(model_id).arun(prompt)
    return response.content


async def llm_call_structured(prompt: str, output_schema: type[BaseModel], model_id: str = DEFAULT_MODEL_ID):
    """Like llm_call, but returns a parsed instance of `output_schema` instead of raw text."""
    response = await _agent(model_id, output_schema).arun(prompt)
    return response.content


def load_prompt(name: str) -> str:
    """Load a prompt template from backend/prompts/ by name, e.g. "section_summarizer" or
    "section_summarizer.md" — resolved by file location, independent of the caller's cwd."""
    filename = name if name.endswith(".md") else f"{name}.md"
    path = os.path.join(PROMPTS_DIR, os.path.basename(filename))
    with open(path) as f:
        return f.read()
