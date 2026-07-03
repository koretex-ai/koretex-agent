# Next steps — resume here

*Last updated: 2026-07-03. Written as a hand-off so a fresh session can continue without re-deriving context. Read this + the README + docs/phase{0,1}-findings.md + docs/model-eval.md to be fully caught up.*

## Where we are (one paragraph)

The kernel (Phase 1) is built and validated end-to-end. A full csv2json mission ran to a passing terminal review with all roles on Qwen3.6-35B-A3B **Q4**, served from the user's RTX 3090 and consumed over the Koretex network (`https://dispatcher.koretex.ai/v1`). The escalation-ladder tiering is empirically confirmed: concierge = Qwen3-4B, small-node workhorse = qwen3:14b, premium/orchestrator tier = 35B-A3B (fails at Q2 on 18GB, honest at Q4 on 24GB+). Everything is committed/pushed to `koretex-ai/koretex-agent`.

## How to run things (env + commands)

```bash
cd ~/code/koretex-agent
# venv with the kernel installed editable + pytest:
phase0/.venv/bin/python -m pytest tests/ -q         # unit + budget gate (should be 11 passing)

# point the kernel at a model (env vars read by client.py):
export KORETEX_API_KEY=$(python3 -c "import json;print(json.load(open('/Users/moreshkokane/.koretex/customer.json'))['key'])")
export KORETEX_AGENT_BASE_URL=https://dispatcher.koretex.ai/v1                      # or http://localhost:11434/v1 for local
export KORETEX_AGENT_MODEL=hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M             # or qwen3:14b-16k locally

# single profile:
phase0/.venv/bin/koretex-agent validator --workdir <dir> --task "..." --assert "VAL-001|stmt|cmd"
# full mission (run detached so a session restart can't kill it):
nohup bash phase0/run-mission-35b.sh > phase0/mission-35b.log 2>&1 & disown
```

Local models available (Ollama, Mac): `qwen3:4b`, `qwen3:14b`, `qwen3:14b-16k`, `qwen36-35b:16k` (Q2 — fragile), `gemma3:12b-it-qat`.
Dispatcher model for the 35B Q4: `hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M` (served from the 3090).

## Environment gotchas (don't rediscover these)

- **3090 node stays up only via the admin-installed scheduled tasks** (KoretexOllama + KoretexNodeAgent). Managed engine is on port **11435**, separate from system Ollama (11434). If the dispatcher `/healthz` shows `nodes:0` or "no node available", the Windows node dropped — check `koretex status` on that box.
- **Mac 35B-Q2 local run** needs `sudo sysctl iogpu.wired_limit_mb=15360` (resets on reboot). Not needed for the network path.
- **llama.cpp grammar can't handle regex `pattern`/length constraints** — `schemas.response_schema()` already strips them; pydantic validates client-side. Keep that when adding schemas.
- Kernel session strips reasoning fields from history (`strip_thinking`); trajectories land in `~/.koretex-agent/trajectories/*.jsonl`.

## Next steps (priority order)

### 1. Disable thinking for worker/validator; keep it for orchestrator  ← do first, cheap + high impact
The 35B mission burned ~288K tokens largely on Qwen3.6 thinking-mode preambles across ~12 sessions. Thinking helps the orchestrator (planning judgment) but is wasteful for the mechanical worker/validator roles.
- Add a per-profile `thinking: bool` flag in `profiles/__init__.py`.
- In `session.py`/`client.py`, when thinking is off, append `/no_think` to the system or first user message (Qwen convention — verified working: `"say pong /no_think"` returned clean "pong"). Confirm it actually suppresses the `<think>` block for this GGUF via the dispatcher.
- Re-run the mission; compare token count (expect a large drop) and confirm quality holds.

### 2. Deterministic plan-lint (carried from Phase 1)
The orchestrator sometimes emits weak/broken contracts (14B: `pytest || true`, bare `test -f`; 35B: a self-contradictory grep, and it put the command in `statement` and left `command` null). Add a code-level lint between `plan()` and execution that rejects:
- vacuous assertions (`|| true`, bare existence checks with no behavioral check),
- assertions with neither a `command` nor an executable-looking `statement`,
and bounces the plan back to the orchestrator with the objection (one retry). Also tighten the orchestrator prompt so `command` gets populated (statement = prose, command = the check).

### 3. Concierge → mission wiring (tier 0 drives the ladder)
Build the routing entrypoint: a `concierge` profile/loop (Qwen3-4B) that takes a user message, emits the `Route` schema (concierge/task/mission — already prototyped and 5/5 correct), and dispatches: chat locally, or spin a single worker (tier 1), or a full mission (tier 2). This is the first piece of the actual product UX. Escalation triggers between tiers are in the README design.

### 4. The learning loops (start the data flywheel)
- **Skill synthesis**: after a gate-passed mission, a `skill-synthesizer` profile distills the trajectory into an agentskills.io Markdown skill; add a skills catalog + win/loss ledger (skills already loadable via the `use_skill` tool).
- **Trajectory harvest**: the `(contract, trajectory, verdict)` triples are already being written. Build the filter → per-role SFT/DPO dataset pipeline in `training/` (Hermes's batch_runner/trajectory_compressor are reference implementations). This is the path to post-training the 15B brain.

### 5. Model strategy follow-through
- Decide the standard-tier model for the network: Qwen3-14B (small-node local) vs. running everything through the 35B-Q4 network tier. Likely both, per the ladder.
- The ~16GB orphaned Q3_K_M blobs in `~/.ollama` on the Mac are still there (failed pull, wrong tag). Clean up if disk matters (no `ollama` prune; manual blob deletion is risky).

## Open design questions to settle

- **Escalation trigger tuning**: when does tier-1 (single worker) become tier-2 (mission)? When does a mission escalate a step to tier-3? Currently the CLI picks the profile manually; the concierge should decide.
- **Thinking-mode policy per tier** (ties to step 1): orchestrator on, workers/validators off — confirm empirically.
- **Where the orchestrator runs**: on the premium tier (35B) always, or only when a mission is "hard"? Phase 1 showed the 14B orchestrator is weak; 35B is strong. Cost vs quality.
- **User-trajectory privacy**: still own-machines-only until an explicit opt-in is designed.

## Repo state
- Branch: `main`, pushed to `github.com/koretex-ai/koretex-agent`.
- Tests: 11 passing (`phase0/.venv/bin/python -m pytest tests/ -q`).
- Docs: README (architecture), phase0-findings, phase1-findings, model-eval, this file.
- Kernel code: `koretex_agent/{client,tools,session,trajectory,mission,budget,cli,schemas}.py` + `profiles/`.
