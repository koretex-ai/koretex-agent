#!/usr/bin/env python3
"""Consent-gated, scrubbed dataset export.

  # one-time: record consent (own hardware contributes by default)
  phase0/.venv/bin/python scripts/export-datasets.py --grant own

  # dry run (write scrubbed bundle locally, no upload)
  phase0/.venv/bin/python scripts/export-datasets.py

  # upload to Hetzner (source creds first)
  source ~/.koretex-agent/hetzner.env
  phase0/.venv/bin/python scripts/export-datasets.py --upload

Credentials come only from the environment. See koretex_agent/export.py."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent.consent import set_consent  # noqa: E402
from koretex_agent.export import export  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(prog="export-datasets")
    ap.add_argument("--grant", choices=["own", "user"],
                    help="record consent to contribute (own hardware or explicit user opt-in) and exit")
    ap.add_argument("--revoke", action="store_true", help="record a decline and exit")
    ap.add_argument("--out", default=str(ROOT / "phase0" / "export"))
    ap.add_argument("--upload", action="store_true", help="upload the bundle to S3 (needs HETZNER_* env)")
    args = ap.parse_args()

    if args.revoke:
        set_consent(contribute=False, scope="user", note="revoked via CLI")
        print("consent: contribution declined."); return
    if args.grant:
        c = set_consent(contribute=True, scope=args.grant, note="granted via CLI")
        print(f"consent recorded: contribute=True scope={c.scope} at {c.updated}"); return

    mission_workdirs = [p.parent.parent for p in ROOT.glob("phase0/**/.mission/state.json")]
    result = export(args.out, mission_workdirs=mission_workdirs, upload=args.upload)

    print("manifest:")
    print(json.dumps(result["manifest"], indent=2))
    if args.upload:
        print(f"\nuploaded to prefix {result['prefix']}:")
        for k in result["uploaded"]:
            print(f"  {k}")
    else:
        print(f"\nwrote bundle to {result['out_dir']} (dry run — pass --upload to send)")


if __name__ == "__main__":
    main()
