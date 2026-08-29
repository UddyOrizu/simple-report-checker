from app.llm.client import llm_call_structured, load_prompt
from app.schemas.claim import RoutingDecision

_PROMPT_TEMPLATE = load_prompt("router")


async def route_claim(claim_text: str, context: str) -> RoutingDecision:
    prompt = _PROMPT_TEMPLATE.format(context=context, claim_text=claim_text)
    print(f"Sending request to LLM with prompt: {prompt}")
    return await llm_call_structured(prompt, RoutingDecision, tier="standard")
