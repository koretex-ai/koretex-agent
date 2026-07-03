#!/usr/bin/env python3
"""Distil a completed mission into a reusable skill (loop 2).

  export KORETEX_AGENT_BASE_URL=... KORETEX_AGENT_MODEL=... KORETEX_API_KEY=...
  phase0/.venv/bin/python scripts/synthesize-skill.py <mission-workdir>

Writes the skill to ~/.koretex-agent/skills/<name>/SKILL.md and registers it in
the win/loss ledger. See koretex_agent/skills.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent.client import Client  # noqa: E402
from koretex_agent.skills import render_skill_md, synthesize_from_mission  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: synthesize-skill.py <mission-workdir>")
    workdir = sys.argv[1]
    skill, path = synthesize_from_mission(workdir, client=Client())
    print(f"wrote skill '{skill.name}' -> {path}\n")
    print(render_skill_md(skill))


if __name__ == "__main__":
    main()
