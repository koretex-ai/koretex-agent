# Read the customer key from the local secret file — never hardcode it (this
# repo is public). Falls back to an already-exported KORETEX_API_KEY.
export KORETEX_API_KEY="${KORETEX_API_KEY:-$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.koretex/customer.json')))['key'])")}"
export KORETEX_AGENT_BASE_URL=https://dispatcher.koretex.ai/v1
export KORETEX_AGENT_MODEL=hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M
cd /Users/moreshkokane/code/koretex-agent
phase0/.venv/bin/koretex-agent mission --workdir phase0/mission-35b   --task "Build a Python CLI tool csv2json: cli.py reads a CSV file path argument and prints a JSON array of row objects to stdout; a --pretty flag produces indented output; quoted fields containing commas and missing values must be handled correctly; include a pytest test suite under tests/ (verify with python3 -m pytest if available, else run cli.py directly on sample files); include a README.md with usage examples. Use only the Python standard library."
