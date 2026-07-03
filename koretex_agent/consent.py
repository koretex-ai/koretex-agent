"""Consent gating for dataset export. Nothing leaves a machine unless consent is
explicitly recorded here. The design: the team's own machines are contributed by
default (`scope="own"`), a user's machine only via an affirmative opt-in
(`scope="user"`). The consent record is a small local file, auditable, and the
export refuses to run without it."""
from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel

DEFAULT_CONSENT_PATH = Path.home() / ".koretex-agent" / "consent.json"


class Consent(BaseModel):
    contribute: bool
    scope: str  # "own" (team hardware) | "user" (explicit opt-in)
    note: str = ""
    updated: str = ""


class ConsentError(RuntimeError):
    """Raised when an export is attempted without recorded consent."""


def load_consent(path: Path = DEFAULT_CONSENT_PATH) -> Consent | None:
    if not path.exists():
        return None
    return Consent.model_validate_json(path.read_text())


def set_consent(contribute: bool, scope: str, note: str = "",
                path: Path = DEFAULT_CONSENT_PATH) -> Consent:
    if scope not in ("own", "user"):
        raise ValueError("scope must be 'own' or 'user'")
    c = Consent(contribute=contribute, scope=scope, note=note,
                updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(c.model_dump_json(indent=2))
    return c


def require_consent(path: Path = DEFAULT_CONSENT_PATH) -> Consent:
    """Return the consent record, or raise if contribution is not granted."""
    c = load_consent(path)
    if c is None:
        raise ConsentError(
            f"no consent on file at {path}. Record it before exporting: "
            "set_consent(contribute=True, scope='own'|'user')."
        )
    if not c.contribute:
        raise ConsentError("consent on file declines contribution (contribute=false).")
    return c
