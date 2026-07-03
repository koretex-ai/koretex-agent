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

### 1. Disable thinking for worker/validator; keep it for orchestrator  — ✅ DONE (2026-07-03)
The 35B mission burned ~288K tokens largely on Qwen3.6 thinking-mode preambles across ~12 sessions. Thinking helps the orchestrator (planning judgment) but is wasteful for the mechanical worker/validator roles.

Implemented:
- Per-profile `thinking: bool` in `profiles/__init__.py` — worker/validator/scrutiny = `False`, orchestrator = `True`.
- **Mechanism corrected during implementation:** the `/no_think` text switch does NOT work through either serving path. Empirically, on the dispatcher's llama.cpp it is treated as literal prompt text and the model reasons *more* (307→627 completion tokens); on Ollama's OpenAI endpoint it's a no-op. The prior "verified working" note was a false positive (the answer was clean but the `<think>` block wasn't checked). What actually works is the OpenAI-standard **`reasoning_effort: "none"`** request param, honored by both serving paths: dispatcher 35B **307→14** completion tokens, local qwen3:14b **380→14**, `<think>` block gone. `chat_template_kwargs.enable_thinking=false` did NOT work on the dispatcher.
- Wiring: `Client.chat` takes `reasoning_effort`; `run_session`/`constrained_call` map `thinking=False → "none"` and apply it to **every** call in a session (agentic turns + terminal handoff), so reasoning can't leak back mid-session. Orchestrator `plan()`/`revise()` go through `constrained_call` with the default (unset) → thinking stays on.
- Verified end-to-end: a worker session on qwen3:14b created `hello.py` correctly with `done:true` in ~476 completion tokens across 21 calls (~23/call — clearly no thinking). Tests added in `tests/test_kernel.py` (per-tier policy, effort mapping, per-call propagation); suite now 14 passing.

**Full-mission re-run (2026-07-03) — measured, and it corrects the prediction.** Re-ran the same csv2json brief on the 35B via the dispatcher, thinking off for workers/validators. Result vs the thinking-on baseline (prompt 220,629 + completion 66,989 = 287,618):
- No-think: prompt 262,370 + completion **21,650** = 284,020.
- **Completion (generated) tokens: −68%** (66,989 → 21,650). This is the real win — generation is the slow autoregressive part, so it's a genuine latency/compute saving on the node.
- **Total tokens: essentially flat (−1%)** — the doc's "expect a large drop in total" did NOT hold. Prompt/context tokens dominate the bill and rose +19% this run.
- Quality held: all 3 tasks cleared on attempt 1 (0 retries), terminal review passed 8/8, and the deliverable runs correctly (quoted-comma + missing-value handling, `--pretty`, 16-test suite passes).
- Caveat: one run each, not a clean A/B — the prompt-token rise is run-to-run variance in execution path (turn count), not caused by thinking mode. Workers can loop to `max_turns` and re-send accumulated context each turn.

**Takeaway that reshapes priorities:** with thinking off, the dominant cost is now prompt/context tokens driven by **turn count**, not reasoning. Early worker termination (workers hitting `max_turns` instead of stopping when done) is likely a bigger token lever than thinking mode was — see new step 1b.

### 1b. Make workers terminate when done — ✅ prompt fix landed (2026-07-03), full-mission confirm still open
**Investigation (from the no-think mission trajectories):** most sessions DO stop early; the problem is specific. The first worker (T01) hit `max_turns=20` without ever stopping — but it finished all real work by turn 13 (cli.py written, 16 tests passing, README done). Turns 14–20 were pure ceremony: it re-verified each contract assertion **one at a time, one per turn** (VAL-001, VAL-002, …), and ran out of turns still doing it. Every ceremonial turn re-sends the full accumulated context (all file contents it wrote) — that's the prompt-token sink. The terminal validator similarly maxed at 12 turns.

**Fix:** rewrote `profiles/worker.md` — verify once and batch related checks into a single command; do NOT re-verify assertion-by-assertion across turns (independent validators re-check everything anyway); STOP the moment executed output shows the assertions pass ("stopping early is correct, not lazy"). Worker prefix budget unaffected (549/3000).

**Measured (same T01 contract, same 35B via dispatcher, single-worker probe):**
- Baseline (maxed at 20 turns): prompt 81,579 + completion 3,874 = **85,453**.
- Fixed (stops at 8 turns, done=true, work correct): prompt 15,132 + completion 979 = **16,111**.
- **−81% for that worker session.** Prompt tokens dominate the saving (81,579 → 15,132) because they compound with turn count.
- Caveats: N=1, and the baseline was the worst-case maxed-out session (most sessions already stopped fine), so the mission-wide drop will be smaller than 81% — but that first worker was ~30% of the whole mission's tokens, so the dent is large.

**Full-mission confirm (run `m1`, 2026-07-03) — the prompt fix is NOT the main lever, and the root cause is elsewhere.** m1 came in at **415K tokens (worse than the 288K baseline)**; 2 of 4 workers still maxed at 20 turns. But investigating those two workers reframed everything: they were NOT doing ceremonial re-verification — they were honestly stuck fighting a **broken orchestrator-emitted assertion** (VAL-006: `grep -q 'usage'` is case-sensitive and fails on the "Usage" heading; plus confusing `'\-\-pretty'` escaping). The deliverable was correct; the contract was wrong. Conclusions:
- The 1b prompt fix (kept — it helps genuine ceremonial cases, T2 shows 8 turns) does not address these blowups.
- **Do NOT add a structural turn-cap backstop** as the primary fix — it would truncate a worker legitimately struggling and mask the bad contract behind a clean cap.
- The real lever is **step 2 (plan-lint)** below — kill fragile assertions at the source. And give the worker an escape hatch (see step 2, added rule).
- Mission-level numbers swing wildly (284K↔415K) on stuck-worker spirals + stochastic retries; consistency repeats (m2/m3…) belong AFTER step 2 lands.

### 2. Deterministic plan-lint (carried from Phase 1) — ✅ DONE (2026-07-03)
**Implemented:** `koretex_agent/plan_lint.py` (`lint_plan`/`lint_assertion`), wired into `mission.plan()` as a one-shot repair bounce (orchestrator gets the objections and re-emits; validators stay the real gate, so a still-dirty plan proceeds rather than hard-failing). Rules: self-passing commands (`|| true`/`|| :`), existence-only checks (`test -f` with no behavior), case-sensitive doc greps (require `grep -qi`), `\-\-` escaping, missing `command`, and checks misfiled in `statement`. Orchestrator prompt tightened to avoid all of these at the source; worker.md gained the escape-hatch (request_attention on a broken assertion instead of thrashing). Verified: lint flags m1's real VAL-006 (both defects) and leaves the 5 good assertions clean; 23 tests pass; budgets fine (orchestrator 306/5000, worker 633/3000). **Still open:** run the consistency repeats (m2/m3…) now that this has landed — that's where we confirm the mission-level token numbers stabilize.

<details><summary>original notes</summary>
The orchestrator sometimes emits weak/broken contracts (14B: `pytest || true`, bare `test -f`; 35B: a self-contradictory grep, and it put the command in `statement` and left `command` null). **New hard evidence from mission run `m1` (2026-07-03):** the orchestrator emitted VAL-006 as `[ -f README.md ] && grep -q 'usage' README.md && grep -q '\-\-pretty' README.md`. That single fragile assertion cost ~26 wasted worker turns across two sessions (both maxed at 20) and helped push the mission to 415K tokens — the deliverable was correct the whole time. **This is the highest-leverage fix, not worker/thinking tuning.**

Add a code-level lint between `plan()` and execution that rejects:
- vacuous assertions (`|| true`, bare existence checks with no behavioral check),
- assertions with neither a `command` nor an executable-looking `statement`,
- **case-sensitive prose greps on documentation** — `grep -q '<lowercase word>'` against README/docs is brittle (headings capitalize); require `grep -qi` or ban gating on doc *wording* entirely,
- **needless/confusing escaping** like `'\-\-pretty'` (should be `grep -- '--pretty'` or `grep -F`),
and bounces the plan back to the orchestrator with the objection (one retry). Also tighten the orchestrator prompt so `command` gets populated (statement = prose, command = the check) and so documentation assertions check for *existence of the file + a section*, not exact prose casing.

**Also add a worker escape hatch (small, complementary):** worker.md step 4 already says to `request_attention=true` when the order "conflicts with reality," but in m1 the workers didn't recognize a *buggy assertion* as such — they treated it as their own bug and thrashed. Add explicit guidance: if the artifact is honestly produced and correct but an assertion still fails in a way that looks like the assertion itself is wrong (e.g. a case-sensitive grep vs a valid heading), stop and `request_attention` with the evidence rather than fighting it to `max_turns`.
</details>

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
- Tests: 14 passing (`phase0/.venv/bin/python -m pytest tests/ -q`).
- Docs: README (architecture), phase0-findings, phase1-findings, model-eval, this file.
- Kernel code: `koretex_agent/{client,tools,session,trajectory,mission,budget,cli,schemas}.py` + `profiles/`.
