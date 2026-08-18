"""CLI: python scripts/run_classify.py "claim text" [--section "section title"]

Classifies a claim's domain via the three-tier cascade (structural -> terminology -> semantic)
and prints which tier resolved it and why. --section supplies the claim's section title, which
only resolves the structural tier if the title itself carries a recognizable domain cue (see
infer_domain_hint_from_title) — a generic title like "Executive Summary" falls through to the
next tier, same as having no section at all.
"""

import argparse
import os
from dataclasses import dataclass

import yaml

from app.nlp.domain_router import classify_domain, infer_domain_hint_from_title

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")


@dataclass
class _Section:
    domain_hint: str | None


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify a claim's domain via the cascade.")
    parser.add_argument("claim_text", help="The claim text to classify")
    parser.add_argument("--section", default=None, help="The claim's section title, if any")
    args = parser.parse_args()

    registry = yaml.safe_load(open(REGISTRY_PATH))
    section = _Section(domain_hint=infer_domain_hint_from_title(args.section)) if args.section else None

    result = classify_domain(args.claim_text, section, registry)

    print(f"domain: {result['domain']}")
    print(f"confidence: {result['confidence']:.2f}")
    print(f"resolved via: {result['source']} tier")
    if section is not None:
        print(f"(section title '{args.section}' -> domain_hint={section.domain_hint!r})")


if __name__ == "__main__":
    main()
