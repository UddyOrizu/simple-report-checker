"""CLI: python scripts/run_extract.py "sentence" [--context "context capsule"]

Runs claim decomposition standalone against a single sentence and prints each resulting claim
as a labeled card, scope tag front and center.
"""

import argparse
import asyncio

from app.agents.decomposer import decompose_sentence


async def run(sentence: str, context_capsule: str) -> None:
    result = await decompose_sentence(sentence, context_capsule)
    for i, claim in enumerate(result.claims, start=1):
        print(f"[{i}] scope={claim.scope}  claim_type={claim.claim_type}")
        print(f"    text: {claim.text}")
        print(f"    source_span: {claim.source_span}")
        print(f"    requires: {', '.join(claim.requires)}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run claim decomposition against a single sentence.")
    parser.add_argument("sentence", help="The sentence to decompose")
    parser.add_argument("--context", default="", help="Context capsule (section title + document title)")
    args = parser.parse_args()

    asyncio.run(run(args.sentence, args.context))


if __name__ == "__main__":
    main()
