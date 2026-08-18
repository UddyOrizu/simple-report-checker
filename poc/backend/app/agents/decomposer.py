from app.llm.client import llm_call_structured, load_prompt
from app.schemas.claim import ClaimList

DECOMPOSER_MODEL_ID = "claude-sonnet-5"

_PROMPT_TEMPLATE = load_prompt("decomposer")


async def decompose_sentence(sentence: str, context_capsule: str) -> ClaimList:
    prompt = _PROMPT_TEMPLATE.format(context_capsule=context_capsule, sentence=sentence)
    return await llm_call_structured(prompt, ClaimList, model_id=DECOMPOSER_MODEL_ID)
