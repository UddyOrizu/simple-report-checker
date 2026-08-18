from app.nlp.clause_filter import needs_decomposition

EXAMPLE_A_SENTENCE = (
    "Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost discipline, "
    "positioning us ahead of our closest competitor."
)


def test_example_a_sentence_flags_needs_decomposition():
    assert needs_decomposition(EXAMPLE_A_SENTENCE) is True


def test_plain_single_fact_sentence_does_not_flag():
    assert needs_decomposition("Revenue grew 12% year over year.") is False
