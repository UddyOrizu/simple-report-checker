import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim
from app.retrieval.internal_index import lookup_internal_evidence

_MONEY_PATTERN = re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*([MBK])?", re.IGNORECASE)
_PERCENT_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_UNIT_MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9}


def resolves_deterministically(domain: str | None, claim_type: str, registry: list[dict]) -> bool:
    """True if domain_registry.yaml names verification_method: deterministic for this
    (domain, claim_type) pair — the caller's routing decision for whether to even try this path."""
    for row in registry:
        if row.get("domain") == domain and row.get("claim_type") in (claim_type, "*"):
            return row.get("verification_method") == "deterministic"
    return False


def parse_money(text: str) -> float | None:
    match = _MONEY_PATTERN.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").upper()
    return value * _UNIT_MULTIPLIERS.get(unit, 1)


def parse_stated_percent(claim_text: str) -> float | None:
    match = _PERCENT_PATTERN.search(claim_text)
    return float(match.group(1)) if match else None


def extract_current_prior(table_text: str) -> tuple[float | None, float | None]:
    """Finds the row labeled 'current' and the row labeled 'prior' in a table's flattened text
    and parses each one's dollar figure."""
    current = prior = None
    for line in table_text.splitlines():
        lowered = line.lower()
        if current is None and "current" in lowered:
            current = parse_money(line)
        elif prior is None and "prior" in lowered:
            prior = parse_money(line)
    return current, prior


async def verify_deterministic(session: AsyncSession, claim: Claim, thresholds: dict) -> dict:
    """Recomputes (current - prior) / prior from figures located via 5.1's internal lookup and
    compares it to the claim's stated percentage, within thresholds' arithmetic_tolerance_pct.
    Never calls an LLM — this only runs for claims domain_registry.yaml marks deterministic."""
    evidence = await lookup_internal_evidence(session, claim)
    table_evidence = [e for e in evidence if e.source_type == "internal_table"]

    stated_pct = parse_stated_percent(claim.claim_text)
    if stated_pct is None:
        return {
            "final_verdict": "insufficient",
            "resolved_by": "deterministic",
            "reasoning": "no stated percentage found in the claim text",
            "evidence": evidence,
        }

    for table in table_evidence:
        current, prior = extract_current_prior(table.content_snippet)
        if current is None or prior is None or prior == 0:
            continue
        computed_pct = (current - prior) / prior * 100
        difference = abs(computed_pct - stated_pct)
        verdict = "supported" if difference <= thresholds["arithmetic_tolerance_pct"] else "contradicted"
        return {
            "final_verdict": verdict,
            "resolved_by": "deterministic",
            "computed_pct": computed_pct,
            "stated_pct": stated_pct,
            "difference": difference,
            "evidence": [table],
        }

    return {
        "final_verdict": "insufficient",
        "resolved_by": "deterministic",
        "reasoning": "no usable current/prior figures found in retrieved evidence",
        "evidence": evidence,
    }
