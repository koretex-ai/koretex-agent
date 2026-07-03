"""Loop 2 — skills. After a gate-passed mission, distil the successful
trajectory into a reusable skill (agentskills.io Markdown) that later work can
load via the use_skill tool. Skills carry a win/loss ledger so the library is
curated by measured outcome, not vibes: a skill loaded into a mission that
cleared scores a win, one that failed scores a loss.

Faster than loop 3 (weights): a good skill improves behaviour immediately, with
no retrain."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .client import Client
from .profiles import SKILL_SYNTHESIZER
from .schemas import Skill
from .session import _effort, constrained_call
from .training import DEFAULT_STORE, load_sessions

SKILLS_DIR = Path.home() / ".koretex-agent" / "skills"


# ── render + save ────────────────────────────────────────────────────────
def render_skill_md(skill: Skill) -> str:
    """agentskills.io SKILL.md: YAML frontmatter (name + description) + body."""
    return f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body.rstrip()}\n"


def _ledger_path(catalog: Path) -> Path:
    return catalog / "ledger.json"


def load_ledger(catalog: Path = SKILLS_DIR) -> dict:
    p = _ledger_path(catalog)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_ledger(ledger: dict, catalog: Path) -> None:
    catalog.mkdir(parents=True, exist_ok=True)
    _ledger_path(catalog).write_text(json.dumps(ledger, indent=2))


def save_skill(skill: Skill, catalog: Path = SKILLS_DIR) -> Path:
    """Write <catalog>/<name>/SKILL.md and register the skill in the ledger."""
    skill_dir = catalog / skill.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(render_skill_md(skill))
    ledger = load_ledger(catalog)
    ledger.setdefault(skill.name, {"uses": 0, "wins": 0, "losses": 0,
                                   "description": skill.description})
    _save_ledger(ledger, catalog)
    return path


def record_outcome(skill_names: list[str], won: bool, catalog: Path = SKILLS_DIR) -> None:
    """Update the ledger after a mission that loaded these skills cleared (won) or
    failed (lost)."""
    ledger = load_ledger(catalog)
    for name in skill_names:
        e = ledger.setdefault(name, {"uses": 0, "wins": 0, "losses": 0, "description": ""})
        e["uses"] += 1
        e["wins" if won else "losses"] += 1
    _save_ledger(ledger, catalog)


def catalog_index(catalog: Path = SKILLS_DIR) -> list[dict]:
    """The relevance-ready catalog: name + one-line description + win-rate, best
    first. This is what a planner/worker sees (bodies load just-in-time)."""
    ledger = load_ledger(catalog)
    out = []
    for skill_dir in sorted(p for p in catalog.glob("*") if (p / "SKILL.md").exists()):
        name = skill_dir.name
        desc = ""
        for line in (skill_dir / "SKILL.md").read_text().splitlines():
            if line.startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                break
        stats = ledger.get(name, {})
        wins, losses = stats.get("wins", 0), stats.get("losses", 0)
        total = wins + losses
        out.append({"name": name, "description": desc, "wins": wins, "losses": losses,
                    "win_rate": (wins / total) if total else None})
    out.sort(key=lambda s: (s["win_rate"] is not None, s["win_rate"] or 0), reverse=True)
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def select_skills(task_text: str, catalog: Path = SKILLS_DIR, k: int = 3) -> list[str]:
    """Pick up to k skills relevant to a task: keyword overlap between the task
    and each skill's name+description, tie-broken by win-rate. Only skills with
    real overlap are returned — no irrelevant skills injected."""
    task_words = _words(task_text)
    if not task_words:
        return []
    scored = []
    for s in catalog_index(catalog):
        overlap = len(task_words & _words(s["name"] + " " + s["description"]))
        if overlap:
            scored.append((overlap, s["win_rate"] or 0.0, s["name"]))
    scored.sort(reverse=True)
    return [name for _, _, name in scored[:k]]


# ── synthesis ────────────────────────────────────────────────────────────
def _actions_from_messages(messages: list[dict]) -> tuple[list[str], list[str]]:
    cmds, files = [], []
    for m in messages:
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if fn.get("name") == "run_shell" and args.get("command"):
                cmds.append(args["command"])
            elif fn.get("name") == "write_file" and args.get("path"):
                files.append(args["path"])
    return cmds, files


def _render_actions(sessions) -> str:
    parts = []
    for s in sessions:
        cmds, files = _actions_from_messages(s.messages)
        block = [f"## Task: {s.task}"]
        if files:
            block.append("Files written: " + ", ".join(dict.fromkeys(files)))
        if cmds:
            block.append("Commands run:\n" + "\n".join(f"  $ {c}" for c in cmds[:20]))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def synthesize_skill(task: str, actions: str, client: Client | None = None) -> Skill:
    """One constrained call — distil task + actions into a Skill."""
    client = client or Client()
    msgs = [
        {"role": "system", "content": SKILL_SYNTHESIZER.system_prompt()},
        {"role": "user", "content": (
            f"A task was accomplished and passed independent validation.\n\n"
            f"TASK / BRIEF:\n{task}\n\nWHAT WAS DONE:\n{actions}\n\nEmit the Skill as JSON.")},
    ]
    skill, _ = constrained_call(client, msgs, Skill,
                                reasoning_effort=_effort(SKILL_SYNTHESIZER.thinking))
    return skill


def synthesize_from_mission(workdir: str | Path, client: Client | None = None,
                            catalog: Path = SKILLS_DIR,
                            store: Path = DEFAULT_STORE) -> tuple[Skill, Path]:
    """Distil a *done* mission into a skill and save it. Pulls the passing
    workers' actions from the trajectory store, keyed by mission id."""
    state = json.loads((Path(workdir) / ".mission" / "state.json").read_text())
    if state.get("status") != "done":
        raise ValueError(f"mission is '{state.get('status')}', only 'done' missions are distilled")
    mid = state["mission_id"]
    workers = [s for s in load_sessions(store)
               if s.profile == "worker" and s.mission_id == mid and s.label == "pass"]
    if not workers:
        raise ValueError(f"no passing worker trajectories found for mission {mid}")
    skill = synthesize_skill(state["brief"], _render_actions(workers), client)
    path = save_skill(skill, catalog)
    return skill, path
