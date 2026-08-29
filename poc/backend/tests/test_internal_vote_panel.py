from app.agents.internal_vote_panel import VoteOutcome, tally_votes


def test_majority_wins_with_min_confidence_of_agreeing_voters():
    votes = [
        VoteOutcome("standard", "supported", 0.9, "looks fine"),
        VoteOutcome("mini", "supported", 0.7, "agrees"),
        VoteOutcome("fino1", "contradicted", 0.6, "disagrees"),
    ]
    result = tally_votes(votes, "financial")

    assert result["final_verdict"] == "supported"
    assert result["final_confidence"] == 0.7  # min of the two agreeing voters, not the average
    assert result["agreement"] is False  # not unanimous
    assert result["resolved_by"] == "ensemble_vote"
    assert len(result["voter_breakdown"]) == 3


def test_three_way_split_resolves_to_disputed_at_zero_confidence():
    votes = [
        VoteOutcome("standard", "supported", 0.9, "a"),
        VoteOutcome("mini", "contradicted", 0.7, "b"),
        VoteOutcome("fino1", "insufficient", 0.6, "c"),
    ]
    result = tally_votes(votes, "financial")

    assert result["final_verdict"] == "disputed"
    assert result["final_confidence"] == 0.0
    assert result["agreement"] is False


def test_all_voters_failing_degrades_to_insufficient_not_a_crash():
    votes = [
        VoteOutcome("standard", None, None, "", error="rate limited"),
        VoteOutcome("mini", None, None, "", error="timeout"),
    ]
    result = tally_votes(votes, "general")

    assert result["final_verdict"] == "insufficient"
    assert result["final_confidence"] == 0.0
    assert result["agreement"] is None


def test_one_voter_erroring_still_yields_a_majority_from_the_rest():
    votes = [
        VoteOutcome("standard", "supported", 0.8, "ok"),
        VoteOutcome("mini", "supported", 0.5, "ok too"),
        VoteOutcome("fino1", None, None, "", error="HF_TOKEN is not set — BLOCKED-CREDENTIALS"),
    ]
    result = tally_votes(votes, "financial")

    assert result["final_verdict"] == "supported"
    assert result["final_confidence"] == 0.5
    assert result["agreement"] is True  # unanimous among the voters that actually ran
