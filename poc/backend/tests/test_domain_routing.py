import os
from dataclasses import dataclass

import yaml

from app.nlp.domain_router import classify_domain, infer_domain_hint_from_title


@dataclass
class _Section:
    domain_hint: str | None


def _registry():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")
    return yaml.safe_load(open(config_path))


def test_structural_tier_wins_when_section_has_domain_hint():
    section = _Section(domain_hint="financial")
    result = classify_domain("Something completely unrelated to any gazetteer term.", section, _registry())

    assert result == {"domain": "financial", "confidence": 0.95, "source": "structural"}


def test_terminology_tier_fires_on_gazetteer_match_despite_generic_section():
    section = _Section(domain_hint=infer_domain_hint_from_title("Executive Summary"))
    assert section.domain_hint is None  # generic title carries no structural cue

    result = classify_domain("Our approach complies with GDPR.", section, _registry())

    assert result["domain"] == "legal"
    assert result["source"] == "terminology"
    assert result["confidence"] == 0.85


def test_terminology_tier_fires_with_no_section_at_all():
    result = classify_domain("Our EBITDA improved this quarter.", None, _registry())

    assert result["domain"] == "financial"
    assert result["source"] == "terminology"


def test_semantic_tier_falls_back_when_no_hint_and_no_gazetteer_match():
    result = classify_domain("The team celebrated a successful product launch this month.", None, _registry())

    assert result["domain"] == "general"
    assert result["source"] == "semantic"
    assert 0 <= result["confidence"] <= 1


def test_semantic_tier_confidence_ceiling_applies_to_general_domain():
    result = classify_domain("The team celebrated a successful product launch this month.", None, _registry())

    general_ceiling = next(row["confidence_ceiling"] for row in _registry() if row["domain"] == "general")
    assert result["confidence"] <= general_ceiling


def test_infer_domain_hint_from_title_recognizes_domain_cues():
    assert infer_domain_hint_from_title("Financial Highlights") == "financial"
    assert infer_domain_hint_from_title("Legal Disclosures") == "legal"
    assert infer_domain_hint_from_title("Executive Summary") is None
    assert infer_domain_hint_from_title("Outlook") is None
