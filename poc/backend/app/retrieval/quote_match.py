"""Verifies that an LLM-extracted quote actually appears in the source text — the guard that keeps
vectorless retrieval's citations honest. Adapted from simple-vectorless-rag's evidence.py: exact
match on normalized text first, falling back to the longest fuzzy-matching block."""

import difflib
import re
from dataclasses import dataclass

# Markdown markup the source text carries but a model quoting it may reasonably drop — stripped from
# both sides so formatting never decides a match. Emphasis/code markers hug the words they wrap, so
# they're deleted outright; table pipes and heading/quote markers separate words, so they become
# spaces.
_INLINE_MARKUP_RE = re.compile(r"[*_`]+")
_BLOCK_MARKUP_RE = re.compile(r"[|#>]+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def normalize(text: str) -> str:
    text = _BLOCK_MARKUP_RE.sub(" ", _INLINE_MARKUP_RE.sub("", text))
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class QuoteMatch:
    score: float  # 1.0 = exact (normalized) match; otherwise longest-matching-block overlap
    start: int  # offsets into the *normalized* source text
    end: int


def locate_quote(source_text: str, quote: str) -> QuoteMatch:
    norm_source = normalize(source_text)
    norm_quote = normalize(quote)
    if not norm_quote or not norm_source:
        return QuoteMatch(score=0.0, start=0, end=0)

    idx = norm_source.find(norm_quote)
    if idx != -1:
        return QuoteMatch(score=1.0, start=idx, end=idx + len(norm_quote))

    # autojunk off: on a long section, difflib's popularity heuristic would otherwise junk common
    # characters (spaces, 'e') and badly under-score a genuine near-verbatim quote.
    matcher = difflib.SequenceMatcher(None, norm_source, norm_quote, autojunk=False)
    match = matcher.find_longest_match(0, len(norm_source), 0, len(norm_quote))
    if match.size == 0:
        return QuoteMatch(score=0.0, start=0, end=0)

    # The longest block only anchors where the quote sits; scoring it alone would halve the score
    # of a quote with a single changed character in the middle. Align the quote's full length
    # against the source at that anchor and score the whole window instead.
    start = max(0, match.a - match.b)
    end = min(len(norm_source), start + len(norm_quote))
    window = norm_source[start:end]
    # Fuzzy matching forgives wording drift, never figures: "$150M" against a source saying "$112M"
    # is one character off and would otherwise score ~0.9 — exactly the kind of misquote a claim
    # checker must not accept as verified evidence.
    if set(_NUMBER_RE.findall(norm_quote)) - set(_NUMBER_RE.findall(window)):
        return QuoteMatch(score=0.0, start=start, end=end)
    window_score = difflib.SequenceMatcher(None, window, norm_quote, autojunk=False).ratio()
    return QuoteMatch(score=round(window_score, 2), start=start, end=end)
