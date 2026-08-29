from types import SimpleNamespace

from app.retrieval.chunk_sweep import _redact_claim_sentence


def test_exact_substring_is_redacted():
    chunk_text = "Some intro text. Revenue was $4.2M in FY24. Some trailing context about margins."
    claim = SimpleNamespace(source_span="Revenue was $4.2M in FY24.", claim_text="Revenue was $4.2M in FY24.")

    result = _redact_claim_sentence(chunk_text, claim)

    assert "$4.2M" not in result
    assert "[claim sentence redacted]" in result
    assert "Some intro text." in result
    assert "Some trailing context about margins." in result


def test_reworded_claim_falls_back_to_word_overlap_redaction():
    chunk_text = (
        "Background paragraph here. The company reported that revenue reached 4.2 million "
        "pounds in fiscal year 2024, driven by strong Q4 sales. Unrelated closing remark."
    )
    claim = SimpleNamespace(source_span="", claim_text="Revenue was £4.2M in FY24")

    result = _redact_claim_sentence(chunk_text, claim)

    assert "[claim sentence redacted]" in result
    assert "Unrelated closing remark." in result


def test_unrelated_chunk_is_left_untouched():
    chunk_text = "This paragraph is about headcount and office locations, nothing financial at all."
    claim = SimpleNamespace(source_span="", claim_text="Revenue was £4.2M in FY24")

    assert _redact_claim_sentence(chunk_text, claim) == chunk_text
