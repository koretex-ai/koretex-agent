"""Structural data capture: every session becomes a (contract, trajectory, verdict)
triple on disk. This is loop 3's raw material — not optional logging."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_STORE = Path.home() / ".koretex-agent" / "trajectories"


class TrajectoryRecorder:
    def __init__(self, profile: str, contract: dict[str, Any], store: Path | None = None):
        self.session_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
        self.store = (store or DEFAULT_STORE) / f"{self.session_id}.jsonl"
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self._write({"event": "start", "profile": profile, "contract": contract, "ts": time.time()})

    def _write(self, obj: dict) -> None:
        with self.store.open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")

    def message(self, msg: dict) -> None:
        self._write({"event": "message", "msg": msg, "ts": time.time()})

    def tool_result(self, name: str, args: dict, result: str) -> None:
        self._write({"event": "tool", "name": name, "args": args, "result": result, "ts": time.time()})

    def usage(self, usage: dict) -> None:
        self._write({"event": "usage", "usage": usage, "ts": time.time()})

    def verdict(self, verdict: dict[str, Any]) -> None:
        """The label. For workers: the handoff; for validators: the verdict itself;
        later enriched by gate outcomes."""
        self._write({"event": "verdict", "verdict": verdict, "ts": time.time()})
