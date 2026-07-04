"""Kernel tools. Terse schemas on purpose — every byte here is prefill on every
call, and the prefix budget test counts them. Descriptions state only what the
model can't infer from the name."""
from __future__ import annotations

import subprocess
from pathlib import Path

TOOL_SCHEMAS: dict[str, dict] = {
    "run_shell": {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command in the workdir. Returns stdout+stderr (truncated) and exit code.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file (path relative to workdir).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write full file contents (path relative to workdir). Creates parent dirs. Python files are syntax-checked; errors are returned and MUST be fixed.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    "search_files": {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Regex search across files in the workdir. Returns matching lines with file:line.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    "use_skill": {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": "Load the full body of a skill from the catalog by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current/external info. Returns ranked title, url, snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    "web_fetch": {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return its readable text (HTML stripped, truncated).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
}

MAX_OUTPUT_CHARS = 8_000


class Toolbox:
    """Executes tool calls inside one workdir. One instance per session."""

    def __init__(self, workdir: str, skills_dir: str | None = None, allowed: list[str] | None = None,
                 search_backend=None):
        self.workdir = Path(workdir).resolve()
        self.skills_dir = Path(skills_dir).resolve() if skills_dir else None
        self.allowed = allowed or list(TOOL_SCHEMAS)
        # Resolved lazily on first web_search so a non-research session pays
        # nothing (and env selection happens at call time). Injectable for tests.
        self._search_backend = search_backend

    def schemas(self) -> list[dict]:
        return [TOOL_SCHEMAS[n] for n in self.allowed]

    def _resolve(self, rel: str) -> Path:
        p = (self.workdir / rel).resolve()
        if not p.is_relative_to(self.workdir):
            raise ValueError(f"path escapes workdir: {rel}")
        return p

    def call(self, name: str, args: dict) -> str:
        if name not in self.allowed:
            return f"error: tool {name} not available"
        try:
            out = getattr(self, f"_t_{name}")(**args)
        except Exception as e:
            out = f"error: {e}"
        return out[:MAX_OUTPUT_CHARS] + ("\n[truncated]" if len(out) > MAX_OUTPUT_CHARS else "")

    def _t_run_shell(self, command: str) -> str:
        r = subprocess.run(
            command, shell=True, cwd=self.workdir, capture_output=True, text=True, timeout=120
        )
        return f"exit={r.returncode}\n{r.stdout}{r.stderr}"

    def _t_read_file(self, path: str) -> str:
        return self._resolve(path).read_text()

    def _t_write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        note = f"wrote {len(content)} bytes to {path}"
        if path.endswith(".py"):
            try:
                compile(content, path, "exec")
            except SyntaxError as e:
                note += f"\nSYNTAX ERROR (fix before proceeding): line {e.lineno}: {e.msg}"
        return note

    def _t_search_files(self, pattern: str) -> str:
        r = subprocess.run(
            ["grep", "-rn", "-E", pattern, "."],
            cwd=self.workdir, capture_output=True, text=True, timeout=30,
        )
        return r.stdout or "no matches"

    def _t_use_skill(self, name: str) -> str:
        if not self.skills_dir:
            return "error: no skills library configured"
        p = self.skills_dir / name / "SKILL.md"
        if not p.exists():
            return f"error: unknown skill {name}"
        return p.read_text()

    def _backend(self):
        if self._search_backend is None:
            from .search import backend_from_env
            self._search_backend = backend_from_env()
        return self._search_backend

    def _t_web_search(self, query: str, max_results: int = 5) -> str:
        n = max(1, min(int(max_results), 10))
        results = self._backend().search(query, max_results=n)
        if not results:
            return "no results"
        return "\n".join(f"{i}. {r.title}\n   {r.url}\n   {r.snippet}"
                         for i, r in enumerate(results, 1))

    def _t_web_fetch(self, url: str) -> str:
        from .search import fetch_url
        return fetch_url(url)
