import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(__file__))  # backend/
WATCHED_DIRS = ["config", "prompts"]


def compute_config_hash() -> str:
    """Hashes the combined contents of config/ + prompts/ into one identifier, so pipeline_runs
    and agent_traces rows can be tagged with exactly which config/prompt versions produced them."""
    hasher = hashlib.sha256()
    for dirname in WATCHED_DIRS:
        dir_path = os.path.join(ROOT, dirname)
        for root, dirs, files in os.walk(dir_path):
            dirs.sort()
            for filename in sorted(files):
                path = os.path.join(root, filename)
                hasher.update(os.path.relpath(path, ROOT).encode())
                with open(path, "rb") as f:
                    hasher.update(f.read())
    return hasher.hexdigest()[:16]
