"""Locate the browser-runnable artifact a mission or task produced, so the CLI
can tell the user how to open it (and open it for them on a TTY). Pure
detection — no side effects, no browser launch (that is the CLI's job). The
deliverable policy in profiles/{orchestrator,worker}.md asks for a self-contained
`index.html`; this finds it (or the next best .html) so a normal user never has
to go hunting in the workdir."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping


def _resolve_touched(root: Path, touched: Iterable[str]) -> list[Path]:
    """Map handoff `files_touched` entries (bare name, relative, or absolute) to
    absolute paths under (or at) `root`. Only existing files survive."""
    out: list[Path] = []
    for t in touched:
        p = Path(t)
        p = p if p.is_absolute() else root / p
        if p.is_file():
            out.append(p.resolve())
    return out


def _scan(root: Path) -> list[Path]:
    """Top-level .html in `root` only. Deliberately NOT recursive: the workdir is
    often the user's cwd (possibly a large repo), and a deep walk would be slow
    and could surface an unrelated page. The deliverable policy puts `index.html`
    at the root, and subdir artifacts are found precisely via `touched`."""
    return sorted(p.resolve() for p in root.glob("*.html") if p.is_file())


def _rank(root: Path, htmls: list[Path]) -> list[Path]:
    """Best browser entry point first: root `index.html`, then any `index.html`
    (shallowest), then the shallowest/alphabetically-first page. Deterministic."""
    def key(p: Path):
        rel = p.relative_to(root) if _is_under(root, p) else p
        return (
            0 if p.name.lower() == "index.html" else 1,  # index pages win
            len(rel.parts),                               # shallower wins
            str(rel).lower(),                             # stable tiebreak
        )
    return sorted(htmls, key=key)


def _is_under(root: Path, p: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def detect_primary_artifact(workdir: str | Path,
                            touched: Iterable[str] | None = None) -> Path | None:
    """The browser entry point a run produced, or None if there isn't one.

    When `touched` (the run's files_touched) is given, detection is restricted to
    those files — this avoids mistaking a pre-existing .html in the user's cwd for
    the deliverable. Falls back to scanning the workdir when nothing was reported
    or none of the touched files were .html."""
    root = Path(workdir).expanduser()
    if not root.is_dir():
        return None
    htmls: list[Path] = []
    if touched:
        htmls = [p for p in _resolve_touched(root, touched)
                 if p.suffix.lower() == ".html"]
    if not htmls:
        htmls = _scan(root)
    if not htmls:
        return None
    return _rank(root, htmls)[0]


def file_url(path: str | Path) -> str:
    """A browser-openable file:// URL (spaces/unicode percent-encoded)."""
    return Path(path).resolve().as_uri()


def is_web_artifact(workdir: str | Path,
                    touched: Iterable[str] | None = None) -> bool:
    return detect_primary_artifact(workdir, touched) is not None


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def should_auto_open(is_tty: bool, env: Mapping[str, str] | None = None) -> bool:
    """Whether to launch the browser for the user. The open line + path are always
    printed; this only gates the side effect — auto-open for an interactive user
    who hasn't set KORETEX_NO_OPEN. Off for pipes/CI/SSH-scripted runs so nothing
    unexpected pops up."""
    env = os.environ if env is None else env
    return is_tty and not _truthy(env.get("KORETEX_NO_OPEN"))
