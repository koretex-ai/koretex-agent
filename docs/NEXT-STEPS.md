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

### Consumer product (v0.1.0 → v0.1.5, all released) + what's left — resume here (2026-07-04)

The consumer face is built and shipping as versioned GH-release wheels (`install/install.sh` pulls them). Done: two-client concierge (local routing / network work), curl|bash installer (bundled llama.cpp + Qwen3-4B, launchd/systemd, validated end-to-end from scratch on macOS-arm64), human-readable output, `-v` insights (routing + escalation ladder + orchestrator thinking + per-model tokens), live progress (`⋯` to stderr), output-to-cwd, routing fix (questions → chat, not scripts), and **network reliability slice 1** (`client.py`: split connect/read timeouts + retry-time budget, classified retries that don't retry 4xx, `NetworkError` with friendly messages). Command is **`koretex-agent "<msg>"`** (not `koretex` — collides with the node CLI). 125 tests.

**Remaining, prioritized:**
1. **★ Web-friendly artifacts — ✅ DONE (2026-07-04).** "create a game/app" now defaults to a **self-contained `index.html`** (inline CSS + vanilla JS, no build/deps/network) the user just opens, instead of Python/shell scripts. Built: a web-first **deliverable policy** in `profiles/{orchestrator,worker,concierge}.md` (default to a browser app for anything user-facing; honor explicit language asks; fall back to a script only when a browser genuinely can't do it — real backend/OS/batch; assertions stay **headless**, no browser/DOM/screenshot); `koretex_agent/artifacts.py` (`detect_primary_artifact` — precise via the run's `files_touched`, top-level `index.html` fallback, never a recursive cwd walk; `file_url`; `should_auto_open`); the **preview/open step** — `ConciergeResult.artifact` + a `▶ open in your browser: file://…` line in `render_reply`, and the CLI auto-launches the browser on a TTY (`KORETEX_NO_OPEN` opts out). Tests: `tests/test_artifacts.py` (15). **Suite 140 passing.** *Demonstrated live on the 35B (dispatcher):* "make me a snake game i can play in my browser" routed → task → a real worker produced a runnable, **0-external-ref** `index.html` (canvas + 2D ctx, WASD/arrows, collision, score, localStorage high score) passing 12 headless structural checks; output ended with the open-line. Still feeds the **Electron desktop app** (which will host/preview these).
2. **Web search + deep research + own server.** `web_search`/`web_fetch` tools, pluggable backend (keyless `ddgs` default; Brave/Tavily BYO-key); deep-research flow; **own search server = self-hosted SearXNG**, ideally a Koretex-network service (decentralized, no 3rd-party keys).
3. **Wallet / balance.** Account + buy-credits + balance in the status line (installer already takes `--key`; backend flow is separate).
4. **Network reliability slices 2-6** (slice 1 done): **mission-level resume on transient failure** (highest next — state checkpoints already; make a mid-mission `NetworkError` resumable, not fatal), streaming + stall detection, circuit breaker + `/healthz` preflight, fallback tier, latency/failure observability. (Node/dispatcher *stability* is separate infra — the 3090 was overloaded/flaky all day 2026-07-04.)
5. **Electron app** (wraps this consumer component); Linux(systemd) real-box install; host `install.sh` at get.koretex.ai; Windows via the app.
6. **Deferred/blocked:** tier-3 capability-gap test (needs a BYO-key Larger model); Loop-3 GPU training → brain v1 (separate repo).

### Phase 2 — escalation ladder top + metric — ✅ DONE (2026-07-03)
The most foundational open piece is now built. Two halves:

**Metric (`koretex_agent/tiers.py`).** `Tier` enum (0 concierge · 1 task · 2 mission · 3 escalation) + `TierLedger`: every model call is charged to the tier that made it. `escalation_rate()` = fraction of tokens above tier 2; `within_kpi()` checks it against the **≥90%-of-tokens-at-tier-≤2** floor; `report()` is what the CLI prints and the metric asserts. Wired into `MissionState.ledger` (aggregate `tokens` kept for back-compat): `_count`/`_count_usage` tag every worker/validator/orchestrator call `Tier.MISSION`, escalated work `Tier.ESCALATION`. The concierge builds a ladder-wide ledger (tier-0 routing + tier-1 worker + merged mission tier-2/3) exposed on `ConciergeResult.ledger`.

**Tier-3 mechanism (`Mission._attempt_escalation`).** When tier-2 can't clear a step (attempts exhausted, or a worker still blocked after the one bounded replan), that *one step* is handed to a stronger model: **bounded contract** (same assertions), **state stays local** (same workdir), **one attempt**, and **verification stays at tier 2** (the escalated work still must pass the independent two-lane gate — escalation improves the attempt, it doesn't bypass the check). A per-mission **escalation budget** (`DEFAULT_ESCALATION_BUDGET=2`) + explicit counter/notes keep tier-3 rare. Env-gated via `escalation_client_from_env()` (`KORETEX_AGENT_ESCALATION_MODEL`/`_BASE_URL`/`_API_KEY`); unset → `escalation_client=None` → tier-3 off, missions behave exactly as before (graceful, like the embedder). CLI mission path injects it and prints the tier ledger; concierge missions get it too.

**Triggers/counters at each rung:** T0→T1 (concierge `decide`), T1→T2 (tier-1 worker `done=false`/attention → mission, in `concierge.handle`), **T2→T3 (new)** — logged in `state.escalations` [{task_id, trigger, cleared}] + `state.notes`.

**Tests:** `tests/test_tiers.py` (ledger math, KPI at the 0.90 boundary, merge, JSON round-trip) + `tests/test_escalation.py` (clears a stuck step, disabled-without-client behaves as before, failed escalation still fails, budget-zero blocks, KPI report shape). Faked at `run_session` so the coordinator's tier tagging + ledger run for real. Suite **96 passing**.

**Demonstrated on a live model** (`scripts/probe-escalation.py`): trigger fault-injected (a stuck tier-2 worker) for determinism; everything else real — real validators fail on the missing deliverable, real tier-3 worker produces `hello.py`, real validators then pass, terminal review passes. Result: status `done`, `escalations[0].cleared=true`, ledger **mission 16,773 / escalation 4,282** tokens (escalation_rate 0.20, `within_kpi=false` — correct: a 1-of-1-task forced escalation *should* breach the ≥90% floor; the metric is doing its job).

**Healthy-case demonstrated live on the standard tier** (`scripts/live-escalation-mission.py`, 2026-07-03): a full 2-task mission on the **35B-A3B standard tier** (dispatcher, tier-3 off). Both tasks cleared on **attempt 1**, no escalation, ledger **192,118 tokens 100% at tier ≤2** (escalation_rate 0.0, `within_kpi=true`). The deliverable was a correct hand-written recursive-descent parser passing the exact traps (`2**3**2→512` right-assoc, `-2**2→-4` unary precedence, no `eval`) — the *same task* on which a dense qwen3:14b produced syntactically-broken code and couldn't converge (that failed run is why the 14B rung was dropped; see step 5). This is the healthy complement to the probe's breach case: together they show both KPI states on real models.

**Still open (Phase 2 polish, lower priority):** (a) the genuine *capability-gap* escalation (standard tier fails a hard step, a **Larger** model clears it) — deferred until a BYO-key Larger endpoint exists; the calc/parser task here is too easy for the 35B, so this needs a harder task + the stronger model (tracked as the TODO in step 5); (b) a tier-1 quick-check before accepting a task result (mentioned under step 3); (c) surface the escalation-rate across many missions as the learning-loop scoreboard.

### Efficiency — wire elision + batched validation — ✅ DONE (2026-07-04)
The live 35B mission was **89% prompt tokens** (170,940 of 192,118), compounding ~O(turns²) because every agentic turn re-sends the whole transcript. Two fixes:

- **Wire elision (`session._elide_stale_context`).** Keeps the last 3 *turns* full, elides the bulk of older ones on BOTH sides: stale tool *results* (reader/validator cost) and stale assistant tool_call *arguments* — chiefly `write_file` content (the writer/worker cost). **First cut was wrong** — it elided only tool results, which measured **0%** on a real worker: a worker's cost is the full file re-sent in its `write_file` args every turn. Corrected version measured **67%** on that 20-turn worker's transcript. Function name + roles/order preserved; full history still recorded to the trajectory (training data intact).
- **Batched validation (validator.md/scrutiny.md).** "Run every check in ONE shell call, first" is now a hard first-action rule (was a soft hint the 35B ignored — terminal review took 12 turns).

**Controlled A/B (`scripts/ab-elision.py`, 2026-07-04):** both arms run the SAME injected fixed plan (orchestrator skipped → no plan stochasticity) on the 35B; only elision differs. Result — **both arms `done` with a correct deliverable (no quality loss)**; totals 48,740 (off) → 40,817 (on) = −16% live; deterministic replay (noise-free, same trajectory) = **23% saved**. Batching: validators/scrutiny/**terminal review all ran in 2–4 turns** (vs the non-batched baseline's 12-turn review) — the bigger lever. Elision's win scales with turn count (23% clean worker → 67% thrashing worker). Also bumped `client.max_retries` 3→5 so transient network 5xx stop killing whole missions. Suite 103 passing.

**Orchestrator planning — investigated, then PARKED (2026-07-04).** Added step-0 instrumentation (`state.planning`: initial vs repair model_calls + tokens) and tried **B (strip reasoning on re-send)**. Findings, all measured on the 35B dispatcher:
- The earlier "~37K planning" was variance, not the norm. Direct instrumentation: a normal planning is **~9–10K** — **~4,600 tokens/call**, of which **~80% is the thinking block** (~3,690 completion). Total = calls × 4,600; calls = 1 + validation retries + repair.
- **B measured 0% here** (deterministic: re-sending the plan message *with* its 3,690-tok `reasoning` field vs stripped → 940 vs 940 prompt tokens). This dispatcher bills reasoning as *completion* on generation but **ignores it as input** on re-send. **B kept as a harmless correctness guard** (helps a serving path that *does* re-tokenize reasoning); not a win here.
- Real levers if ever un-parked: (A) `reasoning_effort:low/medium` — attacks the 80%/call thinking block, but quality-gated (needs a plan-quality A/B); (B) **retry reduction** — even a simple brief hit 1 retry (first grammar-constrained JSON failed pydantic re-validation = a wasted ~4,600-tok call); diagnosing the systematic field mismatch would be a quality-neutral win. Parked because ~9–10K/normal-mission isn't worth the quality risk right now.

**Still open (efficiency, minor):** workers occasionally *thrash* to the 20-turn cap on a genuinely hard step (task-difficulty, not elision); a per-mission prompt-amplification stat in the ledger to keep wire cost visible.

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

### 2b. Validator reliability (surfaced by m2/m3) — ✅ DONE (2026-07-03), real-run validation pending
The m2/m3 consistency repeats proved the worker-side fixes hold (0 worker maxouts both runs, plan-lint robust) but exposed the next tier: **validators**. 1–3 validators max out at 12 turns every run, they now dominate the token budget (mission totals still swing 220K↔383K), and in m2 a **maxed-out terminal validator returned a false FAIL** — "3 tests failed" on a suite that passes 26/26 and that the per-task gate had already cleared.

**Implemented:**
- `run_session` now reports `hit_turn_cap` (ran out of turns without a clean stop → its handoff was produced under duress).
- New `Mission._judge`: runs a validator lane and, if it hit the cap, gives it **one clean re-run**; returns `(handoff, inconclusive)` where `inconclusive` = capped even on the retry.
- `_validate` **ignores an inconclusive lane's FAILs** (the other lane still checks honestly; the event is recorded in `state.notes`). A trustworthy FAIL still bounces the task.
- Terminal review runs through `_judge` at a **higher turn budget** (`TERMINAL_REVIEW_MAX_TURNS=20`, vs 12 for a per-task lane) and, if still inconclusive, does **not** fail the mission — every task already cleared its two-lane gate, so it accepts with a note instead of emitting m2's spurious failure.
- validator.md / scrutiny.md tell the lanes to **batch checks into fewer turns** (still attributing each item's raw output). Budgets fine (validator 426/2500, scrutiny 399/2500).
- Tests: `tests/test_validator_reliability.py` (cap detection, re-run-once, inconclusive handling, spurious-FAIL suppression, conclusive-FAIL still blocks). Suite now 29 passing.

**Validated (2026-07-03):**
- *Prevention (batching)* — real mission `m4`: 178,903 tokens, review PASS, **0 maxed sessions** (validators at 9/2/8 turns, were maxing at 12). Best run yet. Caveat: 2-task plan that run, so not a clean token A/B.
- *Cure (don't trust a cut-off validator)* — since m4 maxed nothing, the safety net was field-tested with a **forced-cap probe** (`scripts/probe-cutoff-validator.py`, validators pinned to cap=2 on local qwen3:14b, plan injected to skip the fragile 14b orchestrator). Result: scrutiny lane hit the cap twice → re-run fired → verdict marked inconclusive and ignored → task cleared → mission `done`, **no spurious failure**. Exactly the m2 case, now handled. `notes` recorded the event.

Both halves of 2b are now demonstrated. 2b is complete.

### 3. Concierge → mission wiring (tier 0 drives the ladder) — ✅ DONE (2026-07-03)
**Implemented:** `Route` schema (chat/task/mission) in schemas.py; `CONCIERGE` profile (tools=(), thinking off, 1.5K budget — 267 used) + `profiles/concierge.md`; `koretex_agent/concierge.py` with `decide()` (one constrained routing call on the small model) and `handle()` (dispatch: chat answered locally · task → one tier-1 worker · mission → full coordinator · **tier-1 shortfall auto-escalates to a mission**). `client` runs the concierge (small/local), `work_client` runs the work (network). CLI: `koretex-agent concierge --task "<message>"`. A deterministic fallback uses the raw message when the model leaves `work` blank (observed on small models).

**Validated:** routing 5/5 on local qwen3:4b (chat/memory → chat, quick edits → task, multi-step builds → mission). Full dispatch end-to-end: "what is 2+2?" → answered locally on the 4B (no network); "create hello.py" → routed to a 14B worker that produced the file, done=true. Tests: `tests/test_concierge.py` (routing parse, all dispatch paths, tier-1→tier-2 escalation, blank-work fallback, budget); suite now 36 passing.

**Still open (Phase 2 territory):** richer escalation triggers (tier-1 quick-check before accepting; mission step → tier-3 network escalation), conversation/memory context for the concierge (currently single-turn), and a separate local concierge model wired in deployment (the `work_client` seam exists).

### 4. The learning loops (start the data flywheel) — 🟡 trajectory harvest DONE (2026-07-03), skill synthesis open
- **Trajectory harvest — ✅ DONE.** `koretex_agent/training.py` realizes loop 3: parses the on-disk `(contract, trajectory, verdict)` triples into `SessionRecord`s, labels worker sessions (self-reported handoff, or authoritative gate outcome when `mission_workdirs` are supplied — labels are joined via `(mission_id, task)` from `state.json`), and builds **worker SFT** (passing trajectories) + **worker DPO** (failed vs passed attempt at the same task). `scripts/harvest-trajectories.py` runs it and writes datasets. Real run over our 103 logged sessions: 47 workers (42 pass / 5 fail, gate-linked across 7 missions) → **42 SFT examples**; 0 DPO pairs (our missions passed att1, so no task has both a pass and a fail to pair — the builder is proven by unit test). Tests: `tests/test_training.py`; suite 41 passing. Datasets are gitignored (reproducible via the script).
  - **Validator + routing datasets — ✅ DONE.** `harvest()` now returns named datasets across three roles. *Validator:* a lane's final verdict on a task is labeled by the gate ground truth; correct + cleanly-terminated verdicts → SFT (cut-off verdicts dropped, per 2b); lane dissent is counted. Real run: **30 validator SFT** (13 validator + 17 scrutiny). *Routing:* the concierge logs each decision + downstream outcome (`~/.koretex-agent/routing`); builder makes routing SFT from verified-correct routes and clean same-prompt DPO from escalation corrections (task→mission → chosen=mission, rejected=task). Logging is live (real chat entries captured); verified routing data accrues with concierge use. Export scrubs/bundles all five datasets — full bundle uploaded to Hetzner.
  - *Remaining:* the actual SFT/DPO training run on a GPU box to produce brain v1 (separate repo — not built here).
- **Consent-gated scrubbed export — ✅ DONE.** `koretex_agent/{consent,scrub,export}.py`: export refuses to run without recorded consent (own-hardware default vs explicit user opt-in); every example is scrubbed of secrets/PII/paths (api keys, bearer/JWT, AWS keys, emails, IPs, home dirs, exact env-secret values) before it leaves the machine; an auditable manifest records stats + redaction tally + consent scope. `scripts/export-datasets.py` uploads the bundle to S3-compatible storage (creds from env only). Verified end-to-end: 48 scrubbed SFT examples uploaded to the Hetzner bucket and listed back, leak-checked clean. Honest framing baked into the code/docs: for a coding agent the trajectory *is* the sensitive data, so scrubbing is defence-in-depth and **consent is the real safeguard**.
- **Skill synthesis — ✅ DONE.** `koretex_agent/skills.py` + `skill-synthesizer` profile: distils a *done* mission's passing-worker actions (pulled from the trajectory store by mission id) into a reusable skill (agentskills.io `SKILL.md`), saves it to the catalog (`~/.koretex-agent/skills`), and tracks a **win/loss ledger** (a skill loaded into a mission that clears scores a win; `catalog_index` ranks by win-rate, bodies load just-in-time via `use_skill`). `scripts/synthesize-skill.py`. Verified end-to-end: distilled mission m4 into a real `csv-to-json-cli` skill (generalized steps + pitfalls), ledger-registered, loadable via `use_skill`.
  - **Auto-wiring — ✅ DONE.** Missions now close the loop themselves (flags `use_skills` / `synthesize_on_pass`, both default on): `select_skills` injects relevant catalog skills into each worker's `WorkOrder.skills`, tie-broken by win-rate; the win/loss ledger is scored on resolution (cleared mission → win for every loaded skill, failed → loss; guarded against double-count on resume); and a passing mission distils a fresh skill best-effort. Verified: the `csv-to-json-cli` skill learned from m4 is now auto-selected for csv2json tasks (and ignored for unrelated ones).
  - **Embedding-based skill relevance — ✅ DONE (2026-07-03).** Replaced keyword-overlap triggering with semantic matching. `koretex_agent/embeddings.py`: an `Embedder` seam over the kernel's OpenAI-compatible client (`Client.embed` → local Ollama `/v1/embeddings`, model `nomic-embed-text`, env `KORETEX_AGENT_EMBED_{BASE_URL,MODEL}`) — **local by design** (skill selection is tier-0; a routing decision must not hit the network work tier, so the embed endpoint defaults to `localhost:11434` independent of `KORETEX_AGENT_BASE_URL`). nomic's asymmetric query/document prefixes applied. `select_skills(embedder=…)` is **hybrid**: cosine primary, keyword floor rescue, win-rate tiebreak; a **calibrated `EMBED_MIN_COSINE=0.58`** floor keeps unrelated skills out (nomic has a compressed range — unrelated pairs 0.40–0.55, real matches 0.60–0.68; floor lands in the gap). Skill doc-vectors cached on disk (`<catalog>/<name>/embedding.json`, model+hash keyed); only the task is embedded live. **Graceful degradation:** `embedder=None` / embed failure / model absent → keyword overlap (embedder marks itself dead after one failure, so it's one fallback per run, not per task) — offline tests unchanged. Wiring: `Mission(embedder=…)` (default None → keyword, keeps the suite deterministic); the CLI `mission` path injects `default_embedder()`. **Calibrated + demonstrated on the real model:** 4-skill catalog, 6 paraphrased queries → every one routed correctly, unrelated k8s query → none, nomic's 0.545 noise (git-rebase vs csv) correctly rejected. Tests: `tests/test_embeddings.py` (cosine, prefixing, mark-dead, semantic-beats-zero-keyword, threshold, keyword rescue, ranking, both fallbacks, cache reuse + model-change invalidation). Suite now **84 passing**. **Deliberately NOT changed:** curation dedup (`_similar`) stays on keyword Jaccard — merging is destructive and cosine-dedup is unsafe in nomic's compressed range without its own calibration.
  - **Background curator — ✅ DONE.** `skills.curate()` + `scripts/curate-skills.py` (deterministic, schedule-run): merges near-duplicate skills (Jaccard on name+description; keep the better record, fold the loser's stats in) and retires proven losers (enough uses, win-rate below the floor) into `<catalog>/_retired/`. Demo: a 4-skill catalog → 2 healthy (duplicate folded 1W→6W survivor; 1W/5L loser retired). *Remaining:* the "improve winners" re-synthesis pass; session search; on-device memory files. (Embedding-based relevance ✅ done — see above.)

### 5. Model strategy follow-through
- **DECIDED (2026-07-03): the ladder is concierge (1–4B) → 35B-A3B (standard tier) → Larger (70B+/BYO-key). The dense-14B rung is dropped.** Empirical basis: on the live full-mission attempt the 14B was both too slow as a thinking-on orchestrator (~8 min just to plan) and too weak as a worker (emitted syntactically-broken parsers, `expression.split()` as a "tokenizer", couldn't converge). The 35B-A3B (~3B active) plans in a fraction of the time at comparable cost and clears the same work — so orchestrator + workers + validators all run on the 35B-A3B standard tier. Follow-on: loop-3's post-training target shifts from dense-14B to the 35B-A3B (datasets are model-agnostic triples, so the flywheel is unaffected — only the base checkpoint changes).
- **TODO — genuine tier-3 capability-gap test (deferred, needs a Larger model).** The live "standard tier can't clear a step, premium can" demo requires a model genuinely stronger than the 35B-A3B, and the network has none yet (checked dispatcher `/v1/models`: largest served is the 35B-A3B itself). User will provide a **BYO-key** Larger endpoint (base_url + model + api_key) later; `escalation_client_from_env` already consumes `KORETEX_AGENT_ESCALATION_{BASE_URL,MODEL,API_KEY}`, so wiring is one env block. When it lands: run `scripts/live-escalation-mission.py` with a **complex** task at the 35B's ceiling (the calc/parser task here is too easy for the 35B) + the Larger escalation, and confirm a natural (non-injected) escalation. Until then, tier-3 is proven by unit tests + the fault-injected probe (`scripts/probe-escalation.py`) and the mechanism is complete.
- The ~16GB orphaned Q3_K_M blobs in `~/.ollama` on the Mac are still there (failed pull, wrong tag). Clean up if disk matters (no `ollama` prune; manual blob deletion is risky).

## Open design questions to settle

- **Escalation trigger tuning**: when does tier-1 (single worker) become tier-2 (mission)? When does a mission escalate a step to tier-3? Currently the CLI picks the profile manually; the concierge should decide.
- **Thinking-mode policy per tier** (ties to step 1): orchestrator on, workers/validators off — confirm empirically.
- **Where the orchestrator runs**: on the premium tier (35B) always, or only when a mission is "hard"? Phase 1 showed the 14B orchestrator is weak; 35B is strong. Cost vs quality.
- **User-trajectory privacy**: still own-machines-only until an explicit opt-in is designed.

## Repo state
- Branch: `main`, pushed to `github.com/koretex-ai/koretex-agent`.
- Tests: 140 passing (`phase0/.venv/bin/python -m pytest tests/ -q`).
- Docs: README (architecture), phase0-findings, phase1-findings, model-eval, this file.
- Kernel code: `koretex_agent/{client,tools,session,trajectory,mission,budget,cli,schemas,embeddings,tiers,artifacts}.py` + `profiles/`.
