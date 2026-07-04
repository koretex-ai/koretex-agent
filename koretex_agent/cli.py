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
import os
import sys
import uuid

from .artifacts import file_url, should_auto_open
from .client import Client, ModelConfig
from .profiles import ALL
from .schemas import Assertion, ValidateHandoff, WorkHandoff, WorkOrder
from .session import run_session
from .tools import Toolbox

HANDOFFS = {"worker": WorkHandoff, "validator": ValidateHandoff, "scrutiny": ValidateHandoff}


def _open_in_browser(path: str) -> None:
    """Best-effort launch of the default browser for a web deliverable. Never
    raises — on any failure the user just clicks the printed file:// link."""
    import subprocess

    url = file_url(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


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
    ap.add_argument("--workdir", default=None,
                    help="output dir (concierge defaults to ~/koretex-agent-work)")
    ap.add_argument("--assert", dest="asserts", action="append", default=[])
    ap.add_argument("--skills-dir")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--json", action="store_true", help="raw JSON output (default: human-readable)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="show insights: routing, escalation ladder, thinking, per-model tokens")
    args = ap.parse_args()

    cfg = ModelConfig()
    if args.model:
        cfg.model = args.model
    if args.base_url:
        cfg.base_url = args.base_url

    # Only the concierge defaults its workdir; the raw profiles need one.
    if args.profile != "concierge" and not args.workdir:
        ap.error("--workdir is required for this profile")

    if args.profile == "mission":
        from .mission import Mission
        from .embeddings import default_embedder
        from .client import escalation_client_from_env

        # Skill relevance embeds locally (see ModelConfig.embed_base_url); falls
        # back to keyword overlap if the embed model isn't available. Tier-3
        # escalation is enabled only when KORETEX_AGENT_ESCALATION_MODEL is set.
        m = Mission(args.task, args.workdir, client=Client(cfg), skills_dir=args.skills_dir,
                    embedder=default_embedder(Client(cfg)),
                    escalation_client=escalation_client_from_env())
        state = m.run()
        print(state.model_dump_json(indent=2))
        print("\n── tier accounting ──")
        print(json.dumps(state.ledger.report(), indent=2))
        return

    if args.profile == "concierge":
        # --task carries the user message. The consumer topology: routing runs on
        # the LOCAL concierge model (bundled llama.cpp — KORETEX_CONCIERGE_*),
        # real work is dispatched to the NETWORK (KORETEX_AGENT_* → dispatcher).
        # If no local concierge is configured, both fall back to the work client.
        from pathlib import Path
        from .client import NetworkError, concierge_client_from_env
        from .concierge import handle

        # Work runs where the user is (cwd) unless they name a dir with --workdir.
        # No hidden auto-folder; chat produces no files at all.
        base = args.workdir or str(Path.cwd())

        # Live progress → stderr (stdout stays clean for the reply / --json).
        quiet = args.json or os.environ.get("KORETEX_JSON")
        def _progress(msg):
            if not quiet:
                print(f"\033[2m⋯ {msg}\033[0m" if sys.stderr.isatty() else f"⋯ {msg}",
                      file=sys.stderr, flush=True)

        work_client = Client(cfg)
        concierge_client = concierge_client_from_env() or work_client
        try:
            result = handle(args.task, workdir=base, client=concierge_client,
                            work_client=work_client, skills_dir=args.skills_dir,
                            progress=_progress)
        except NetworkError as e:
            # Friendly, not a stack trace. Progress is checkpointed, so a mission
            # can be resumed by re-running the same request in the same dir.
            print(f"⚠ {e.friendly}", file=sys.stderr)
            sys.exit(1)
        if args.json or os.environ.get("KORETEX_JSON"):
            print(result.model_dump_json(indent=2))
        else:
            from .concierge import render_reply
            verbose = args.verbose or bool(os.environ.get("KORETEX_VERBOSE"))
            print(render_reply(result, verbose=verbose))
            # Open the browser artifact for an interactive user (KORETEX_NO_OPEN
            # opts out); the file:// link is printed either way.
            if result.artifact and should_auto_open(sys.stdout.isatty()):
                _open_in_browser(result.artifact)
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
