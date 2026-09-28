from app.retrieval.quote_match import locate_quote

SOURCE = "## Revenue\n\nRevenue for the year was **$112M**, up from $100M in the prior year.\n\n| Region | Revenue |\n|---|---|\n| APAC | $40M |"


def test_exact_quote_ignoring_markdown_and_whitespace():
    assert locate_quote(SOURCE, "Revenue for the year was $112M,  up from $100M").score == 1.0


def test_table_row_quoted_without_pipes():
    assert locate_quote(SOURCE, "APAC $40M").score == 1.0


def test_near_verbatim_quote_scores_high():
    # one changed word mid-quote shouldn't halve the score
    assert locate_quote(SOURCE, "Revenue for the year was $112M, up from $100M in the previous year.").score >= 0.85


def test_fabricated_quote_scores_low():
    assert locate_quote(SOURCE, "Revenue for the year was $150M, driven by European expansion.").score < 0.85


def test_empty_inputs():
    assert locate_quote(SOURCE, "").score == 0.0
    assert locate_quote("", "anything").score == 0.0


def test_changed_figure_is_never_verified():
    # one digit off is ~0.9 similar as text, but a misquoted figure is not evidence
    assert locate_quote(SOURCE, "Revenue for the year was $150M, up from $100M in the prior year.").score == 0.0
