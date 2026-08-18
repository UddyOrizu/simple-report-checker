from app.agents.tools.citation_check import citation_fidelity


def test_citation_fidelity_true_on_matching_span():
    cited_span = "Revenue (current period) | $112M"
    claimed_content = "$112M"
    assert citation_fidelity(cited_span, claimed_content) is True


def test_citation_fidelity_false_on_mismatched_span():
    cited_span = "Revenue (prior period) | $100M"
    claimed_content = "$112M"
    assert citation_fidelity(cited_span, claimed_content) is False
