"""Minimal CLI for driving single profiles by hand (the mission tier drives them
programmatically). Example:

  koretex-agent worker --workdir /tmp/x --task "create hello.py that prints hi" \
      --assert "VAL-001|python hello.py prints hi|python hello.py"
  koretex-agent validator --workdir /tmp/x --task "validate hello.py" \
      --assert "VAL-001|python hello.py prints hi|python hello.py"
"""
from __future__ import annotations

import argparse
import json
import uuid

from .client import Client, ModelConfig
from .profiles import ALL
from .schemas import Assertion, ValidateHandoff, WorkHandoff, WorkOrder
from .session import run_session
from .tools import Toolbox

HANDOFFS = {"worker": WorkHandoff, "validator": ValidateHandoff, "scrutiny": ValidateHandoff}


def parse_assert(spec: str) -> Assertion:
    parts = spec.split("|", 2)
    if len(parts) < 2:
        raise SystemExit(f"bad --assert (want 'ID|statement[|command]'): {spec}")
    return Assertion(
        item_id=parts[0],
        statement=parts[1],
        command=parts[2] if len(parts) > 2 else None,
    )


def main() -> None:
    ap = argparse.ArgumentParser(prog="koretex-agent")
    ap.add_argument("profile", choices=[*HANDOFFS, "mission", "concierge"])
    ap.add_argument("--task", required=True,
                    help="the work order (or, for `concierge`, the user message)")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--assert", dest="asserts", action="append", default=[])
    ap.add_argument("--skills-dir")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    args = ap.parse_args()

    cfg = ModelConfig()
    if args.model:
        cfg.model = args.model
    if args.base_url:
        cfg.base_url = args.base_url

    if args.profile == "mission":
        from .mission import Mission
        from .embeddings import default_embedder

        # Skill relevance embeds locally (see ModelConfig.embed_base_url); falls
        # back to keyword overlap if the embed model isn't available.
        m = Mission(args.task, args.workdir, client=Client(cfg), skills_dir=args.skills_dir,
                    embedder=default_embedder(Client(cfg)))
        state = m.run()
        print(state.model_dump_json(indent=2))
        return

    if args.profile == "concierge":
        # --task carries the user message. In deployment the concierge model is a
        # small local one; here the same client serves routing and work unless a
        # separate concierge model is configured.
        from .concierge import handle

        result = handle(args.task, workdir=args.workdir, client=Client(cfg),
                        skills_dir=args.skills_dir)
        print(result.model_dump_json(indent=2))
        return

    profile = ALL[args.profile]

    order = WorkOrder(
        order_id=f"cli-{uuid.uuid4().hex[:8]}",
        task=args.task,
        workdir=args.workdir,
        assertions=[parse_assert(s) for s in args.asserts],
    )
    toolbox = Toolbox(args.workdir, skills_dir=args.skills_dir, allowed=list(profile.tools))
    result = run_session(
        profile.name,
        profile.system_prompt(),
        order,
        toolbox,
        HANDOFFS[profile.name],
        client=Client(cfg),
        max_turns=profile.max_turns,
        thinking=profile.thinking,
    )
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
