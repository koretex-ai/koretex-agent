#!/usr/bin/env python3
"""Run the loop-3 trajectory harvest over the on-disk store and write per-role
post-training datasets. Gate-links labels using any mission workdirs found under
phase0/ (their state.json gives the authoritative task pass/fail).

  phase0/.venv/bin/python scripts/harvest-trajectories.py [out_dir]

Default out_dir: phase0/datasets/. See koretex_agent/training.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent.training import DEFAULT_STORE, harvest, write_datasets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
out_dir = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "phase0" / "datasets")

# Every directory under phase0/ that holds a mission is a source of gate labels.
mission_workdirs = [p.parent.parent for p in ROOT.glob("phase0/**/.mission/state.json")]

result = harvest(store=DEFAULT_STORE, mission_workdirs=mission_workdirs)
paths = write_datasets(out_dir, result)

print("harvest stats:")
print(json.dumps(result["stats"], indent=2))
print(f"\nlinked {len(mission_workdirs)} mission workdir(s) for gate labels")
print("wrote:")
for name, p in paths.items():
    print(f"  {name}: {p}")
