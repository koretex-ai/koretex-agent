#!/usr/bin/env bash
# Repeatable full-mission benchmark. Usage: bash scripts/bench-mission.sh <label>
# Runs the csv2json mission into a fresh workdir phase0/bench/<label> on the 35B
# via the dispatcher, with the current code (step 1 thinking-off + 1b worker-stop).
# See docs/benchmarks.md for the protocol and how to read results.
set -euo pipefail
LABEL="${1:?usage: bench-mission.sh <label>}"
# Read the customer key from the local secret file — never hardcode it (this
# repo is public). Falls back to an already-exported KORETEX_API_KEY.
export KORETEX_API_KEY="${KORETEX_API_KEY:-$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.koretex/customer.json')))['key'])")}"
export KORETEX_AGENT_BASE_URL=https://dispatcher.koretex.ai/v1
export KORETEX_AGENT_MODEL=hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M
cd /Users/moreshkokane/code/koretex-agent
WD="phase0/bench/$LABEL"
rm -rf "$WD" && mkdir -p "$WD"
date -u +"%Y-%m-%dT%H:%M:%SZ start $LABEL" > "$WD/.bench-meta"
phase0/.venv/bin/koretex-agent mission --workdir "$WD" \
  --task "Build a Python CLI tool csv2json: cli.py reads a CSV file path argument and prints a JSON array of row objects to stdout; a --pretty flag produces indented output; quoted fields containing commas and missing values must be handled correctly; include a pytest test suite under tests/ (verify with python3 -m pytest if available, else run cli.py directly on sample files); include a README.md with usage examples. Use only the Python standard library."
date -u +"%Y-%m-%dT%H:%M:%SZ end $LABEL" >> "$WD/.bench-meta"
