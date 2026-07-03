"""Consent-gated, scrubbed dataset export. Ties the loop-3 harvest to the two
safeguards: export refuses to run without recorded consent (consent.py), and
every example is scrubbed of secrets/PII (scrub.py) before it is written or
uploaded. Produces an auditable manifest (counts, redaction tally, consent
scope, timestamp) and optionally uploads the bundle to S3-compatible storage.

Credentials are read only from the environment — never hardcoded, never logged."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .consent import Consent, require_consent
from .scrub import Scrubber, secrets_from_env
from .training import DEFAULT_STORE, harvest


def prepare(store: Path | str = DEFAULT_STORE,
            mission_workdirs: list | None = None) -> dict:
    """Harvest, then scrub every example. Returns the scrubbed bundle + a
    redaction tally."""
    bundle = harvest(store=store, mission_workdirs=mission_workdirs)
    scrubber = Scrubber(secrets_from_env())
    for split in ("sft", "dpo"):
        bundle[split] = [scrubber.obj(ex) for ex in bundle[split]]
    bundle["scrub_counts"] = dict(scrubber.counts)
    return bundle


def write_bundle(out_dir: Path | str, bundle: dict, consent: Consent) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("sft", "dpo"):
        (out_dir / f"worker_{name}.jsonl").write_text(
            "".join(json.dumps(ex) + "\n" for ex in bundle[name]))
    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "consent": consent.model_dump(),
        "stats": bundle["stats"],
        "scrub_counts": bundle["scrub_counts"],
        "files": ["worker_sft.jsonl", "worker_dpo.jsonl"],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _s3_client():
    import boto3
    from botocore.config import Config

    missing = [k for k in ("HETZNER_ENDPOINT_URL", "HETZNER_ACCESS_KEY",
                           "HETZNER_SECRET_KEY", "HETZNER_BUCKET_NAME")
               if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing S3 env vars: {', '.join(missing)} "
                           "(source ~/.koretex-agent/hetzner.env)")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["HETZNER_ENDPOINT_URL"],
        aws_access_key_id=os.environ["HETZNER_ACCESS_KEY"],
        aws_secret_access_key=os.environ["HETZNER_SECRET_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )


def upload_bundle(local_dir: Path | str, prefix: str) -> list[str]:
    """Upload every file in the bundle to s3://<bucket>/<prefix>/. Returns the keys."""
    s3 = _s3_client()
    bucket = os.environ["HETZNER_BUCKET_NAME"]
    keys = []
    for f in sorted(Path(local_dir).iterdir()):
        if f.is_file():
            key = f"{prefix}/{f.name}"
            s3.upload_file(str(f), bucket, key)
            keys.append(key)
    return keys


def export(out_dir: Path | str, *, store: Path | str = DEFAULT_STORE,
           mission_workdirs: list | None = None, upload: bool = False,
           prefix: str | None = None) -> dict:
    """Full flow: require consent → harvest → scrub → write → (optional) upload."""
    consent = require_consent()  # raises if not granted
    bundle = prepare(store, mission_workdirs)
    manifest = write_bundle(out_dir, bundle, consent)
    result = {"manifest": manifest, "out_dir": str(out_dir), "uploaded": []}
    if upload:
        prefix = prefix or f"koretex-datasets/{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        result["uploaded"] = upload_bundle(out_dir, prefix)
        result["prefix"] = prefix
    return result
