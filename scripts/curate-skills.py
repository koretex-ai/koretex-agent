#!/usr/bin/env python3
"""Background skill curator — merge near-duplicate skills and retire losers,
from the win/loss ledger. Deterministic; intended to run on a schedule.

  phase0/.venv/bin/python scripts/curate-skills.py [--min-uses N] [--retire-below F]

Retired skills move to ~/.koretex-agent/skills/_retired/ (auditable, out of
selection). See koretex_agent/skills.py."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent.skills import SKILLS_DIR, catalog_index, curate  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(prog="curate-skills")
    ap.add_argument("--min-uses", type=int, default=3)
    ap.add_argument("--retire-below", type=float, default=1 / 3)
    ap.add_argument("--dup-threshold", type=float, default=0.6)
    args = ap.parse_args()

    before = len(catalog_index(SKILLS_DIR))
    report = curate(SKILLS_DIR, min_uses=args.min_uses,
                    retire_below=args.retire_below, dup_threshold=args.dup_threshold)
    after = len(catalog_index(SKILLS_DIR))

    print(f"catalog: {before} active -> {after} active")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
