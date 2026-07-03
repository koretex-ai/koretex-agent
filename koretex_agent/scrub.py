"""Redact secrets and identifying data from trajectories before they can ever
leave the machine. For a coding agent the trajectory *is* the sensitive thing
(file contents, paths, commands), so this is defence-in-depth, not a promise of
anonymity — consent (see consent.py) is the real safeguard. Scrubbing runs
locally, before export, and its redaction counts go into the export manifest for
auditability."""
from __future__ import annotations

import os
import re
from collections import defaultdict

# (name, pattern, replacement). Order matters — most specific first.
RULES: list[tuple[str, re.Pattern, str]] = [
    ("private_key", re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S), "<redacted:private_key>"),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<redacted:jwt>"),
    ("bearer", re.compile(r"[Bb]earer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer <redacted:token>"),
    ("api_key_sk", re.compile(r"sk-[A-Za-z0-9\-_]{12,}"), "<redacted:api_key>"),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted:aws_key>"),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<redacted:email>"),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<redacted:ip>"),
    ("home_macos", re.compile(r"/Users/[^/\s\"']+"), "/Users/<user>"),
    ("home_linux", re.compile(r"/home/[^/\s\"']+"), "/home/<user>"),
]

# Env vars whose *literal values* must be scrubbed if they appear verbatim.
_SECRET_ENV = ("HETZNER_SECRET_KEY", "HETZNER_ACCESS_KEY", "KORETEX_API_KEY")


def secrets_from_env() -> list[str]:
    return [v for k in _SECRET_ENV if (v := os.environ.get(k)) and len(v) >= 8]


class Scrubber:
    """Stateful so a whole export shares one redaction tally."""

    def __init__(self, extra_secrets: list[str] | None = None):
        self.counts: dict[str, int] = defaultdict(int)
        self.extra = [s for s in (extra_secrets or []) if s and len(s) >= 8]

    def text(self, s: str) -> str:
        if not isinstance(s, str):
            return s
        for lit in self.extra:  # exact known-secret values first
            if lit in s:
                self.counts["env_secret"] += s.count(lit)
                s = s.replace(lit, "<redacted:secret>")
        for name, rx, repl in RULES:
            s, n = rx.subn(repl, s)
            if n:
                self.counts[name] += n
        return s

    def obj(self, o):
        """Recursively scrub every string in a JSON-ish structure."""
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, list):
            return [self.obj(x) for x in o]
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        return o


def scrub_text(s: str, extra_secrets: list[str] | None = None) -> str:
    return Scrubber(extra_secrets).text(s)
