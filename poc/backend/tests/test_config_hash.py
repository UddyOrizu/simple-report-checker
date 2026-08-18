import os

from app.config_hash import compute_config_hash


def test_hash_is_stable_across_calls():
    assert compute_config_hash() == compute_config_hash()


def test_hash_changes_when_a_config_file_changes():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "thresholds.yaml")
    original = open(path).read()
    before = compute_config_hash()

    try:
        with open(path, "a") as f:
            f.write("\n# temporary test edit\n")
        after = compute_config_hash()
        assert after != before
    finally:
        with open(path, "w") as f:
            f.write(original)

    assert compute_config_hash() == before  # restored content hashes the same as before


def test_hash_changes_when_a_prompt_file_changes():
    path = os.path.join(os.path.dirname(__file__), "..", "prompts", "verifier.md")
    original = open(path).read()
    before = compute_config_hash()

    try:
        with open(path, "a") as f:
            f.write("\n\n<!-- temporary test edit -->\n")
        after = compute_config_hash()
        assert after != before
    finally:
        with open(path, "w") as f:
            f.write(original)

    assert compute_config_hash() == before
