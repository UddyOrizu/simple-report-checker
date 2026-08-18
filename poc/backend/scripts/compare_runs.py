"""CLI:
  python scripts/compare_runs.py --document <id> --snapshot
      Re-verifies every existing claim for <id> under the CURRENT on-disk config and saves a
      snapshot named by the current config hash.

  python scripts/compare_runs.py --document <id> --config-a <hash> --config-b <hash>
      Loads two previously-taken snapshots for <id> and prints a claim-by-claim diff (verdict,
      confidence, domain, scope changes).

Typical flow: take a snapshot, edit a config file (e.g. arithmetic_tolerance_pct in
config/thresholds.yaml), take a second snapshot, then diff the two hashes.
"""

import argparse
import asyncio
import os
import uuid

import yaml

from app.tuning.snapshot import diff_snapshots, load_snapshot, take_snapshot

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")


def _load_yaml(name: str):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return yaml.safe_load(f)


def print_diffs(diffs: list[dict]) -> None:
    if not diffs:
        print("No claim verdicts changed between the two config snapshots.")
        return
    print(f"{len(diffs)} claim(s) changed:")
    for d in diffs:
        print(f"\n- {d['claim_text']!r} (id={d['claim_id']})")
        for field, (before, after) in d["changes"].items():
            print(f"    {field}: {before!r} -> {after!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run a document's claims under two config snapshots and diff the results.")
    parser.add_argument("--document", required=True, help="Document UUID")
    parser.add_argument("--snapshot", action="store_true", help="Take a new snapshot under the current on-disk config")
    parser.add_argument("--config-a", help="First config_hash to compare")
    parser.add_argument("--config-b", help="Second config_hash to compare")
    args = parser.parse_args()

    if args.snapshot:
        thresholds = _load_yaml("thresholds.yaml")
        registry = _load_yaml("domain_registry.yaml")
        path = asyncio.run(take_snapshot(uuid.UUID(args.document), thresholds, registry))
        print(f"Saved snapshot: {path}")
        return

    if not (args.config_a and args.config_b):
        parser.error("must supply either --snapshot or both --config-a and --config-b")

    snapshot_a = load_snapshot(args.document, args.config_a)
    snapshot_b = load_snapshot(args.document, args.config_b)
    print_diffs(diff_snapshots(snapshot_a, snapshot_b))


if __name__ == "__main__":
    main()
