# Phase 1 findings — the kernel and mission tier

*Test machine: MacBook Pro M3 Pro, 18 GB. Model: qwen3:14b (Q4_K_M) via Ollama, 16K context. Status: kernel + mission tier built and proven; the full end-to-end exit run was stopped before completion (see below).*

Phase 1 built the runtime from first principles (the build-not-fork decision from [phase0-findings.md](phase0-findings.md)) and drove it live on the same 14B that failed under stock Hermes in Phase 0.

## What was built

- **Kernel** (`koretex_agent/`): one OpenAI-compatible client (retry + client-side schema validation), 5 terse tools with a workdir sandbox and a Python syntax-gate on writes, a bounded session loop that strips reasoning tokens from history, and a trajectory recorder writing every session as a `(contract, trajectory, verdict)` JSONL triple to `~/.koretex-agent/trajectories/`.
- **Profiles**: worker, validator, scrutiny, orchestrator — each a prompt + tool subset + model tier + hard prefix budget. A pytest gate (`tests/test_budgets.py`) fails the build if any profile's assembled prefix exceeds budget.
- **Mission tier** (`koretex_agent/mission.py`): a deterministic coordinator — plan (one constrained call, no tools), sequential tasks, dual independent validator lanes, gates that clear only on agreement, retry-with-regression-feedback (max 3), attention→bounded-replan (one revision per task), fresh-eyes terminal review, and disk checkpointing after every step.

## Prefix budgets — the headline number

| Profile | Prefix (prompt + tool schemas) | Budget |
|---|---|---|
| worker | **475 tokens** | 3,000 |
| validator | 356 | 2,500 |
| scrutiny | 362 | 2,500 |
| orchestrator | 145 | 5,000 |

Stock Hermes, for comparison, carried **~25,000 tokens** of fixed prefix (Phase 0). The worker profile is ~50× leaner. This is the difference that turns a 32K-context 14B from unusable (Phase 0: overflow → compaction → role collapse) into workable.

## What worked live (on the 14B that failed in Phase 0)

Every mechanism was exercised end-to-end on real runs against local Qwen3-14B:

- **Constrained-decoding planning**: the orchestrator produced a valid multi-task plan via `response_format` json_schema in a single call — no agentic wandering, the structural fix for Phase 0's orchestrator collapse.
- **Worker builds real, verified work**: given the csv2json task standalone, the worker wrote a correct CLI + syntactically-valid test suite and *actually verified it* — the trajectory shows 8 tool calls including running pytest and executing the CLI on sample files. (Phase 0's naive loop shipped a broken test file and never noticed.)
- **Dual-validator gate**: on the full mission, task T01 (core cli.py) cleared **both** the live-surface and scrutiny lanes on the first attempt.
- **Validator honesty holds** (the core architectural bet): against the known-broken Phase 0 build, a fresh validator refused to pass it (`overall_passed: false`) with real executed evidence; against a good build it passed 3/3 with verbatim command output.
- **Resume from disk**: the mission process was killed twice by unrelated session restarts and resumed exactly where it stopped both times, preserving task state and the token ledger — no replanning, no lost work.

## Where the 14B is weak (confirms the ladder design)

- **Planner judgment is thin.** The orchestrator's contracts leaned on existence/grep checks (`test -f cli.py`, `grep -q 'import csv'`) rather than behavioral ones, and once emitted `python3 -m pytest tests/ || true` — a vacuously-passing assertion. The machinery is sound; the *judgment* in planning is the weakest role, exactly as the escalation-ladder design predicts (orchestrator is the role to escalate to a bigger model or harden with a deterministic plan-lint).
- **Environment friction becomes "attention."** On the test-suite task, the worker hit a pytest-not-installed wall and (correctly) raised `request_attention` rather than fabricate success — which drove the addition of the attention→replan path and brief-propagation into work orders.

## Why the full run was stopped before completion

The end-to-end csv2json mission (T01 implement → T02 tests → T03 README → terminal review) was **stopped mid-run, before reaching a `done` verdict**, for a practical reason: on this hardware the 14B generates at ~6–13 tok/s (thermal throttling pushed it lower), each worker/validator session runs 3–26 minutes, and a full mission with a likely T02 replan projected to **another 1.5–3 hours** of sustained 100% GPU load. That was judged not worth the machine heat for a result that would mostly re-confirm mechanisms already observed working individually. T01 had cleared its gate; T02/T03 and terminal review were not run to completion.

**Net:** every component of the mission tier is proven to work on a 14B; a single clean full-mission pass on this hardware is not yet on record and is best obtained later on faster inference (a bigger network model, an MoE, or the RTX 3090) rather than by grinding the M3 Pro.

## Follow-ups queued (not yet built)

- **Deterministic plan-lint**: reject vacuous assertions (`|| true`, bare `test -f`, grep-only contracts) and bounce the plan back — a code fix for the planner's weakest habit, no bigger model required.
- **Faster local inference for iteration**: an MoE (Qwen3-30B-A3B class, ~3B active) at a quant that fits 18 GB, or a smaller dense model for the worker/validator roles.
- The `commands_run` handoff field is often left empty by the worker (schema laziness); ground truth is in the trajectory regardless, but worth tightening.

## Phase 1 exit criterion — MET (2026-07-03)

The full csv2json mission — the one stock setups and the local 14B never completed — ran **end-to-end to a passing terminal review** on Qwen3.6-35B-A3B **Q4**, served from an RTX 3090 and consumed entirely through `https://dispatcher.koretex.ai/v1`. First complete mission over the network path.

Run trace (all roles on the 35B Q4, remote):
- **Plan**: orchestrator produced 3 tasks with genuinely *behavioral* contracts — constructs edge-case CSVs (quoted commas, empty values), runs the CLI, greps for correct JSON. A qualitative leap over the 14B's `test -f` / `grep import csv` / `pytest || true` contracts in the earlier Phase 1 run.
- **T01** cleared on attempt 2 after an **attention→replan**: the worker executed a contract check, discovered the *assertion itself was self-contradictory* (grepped for a standalone `"John"` that can't appear in `"Smith, John"`), and raised attention instead of mangling correct code to satisfy a bad test. The orchestrator revised the task; the retry cleared both validator lanes.
- **T02** (pytest suite) cleared first try — the task that stalled the Mac run on missing pytest; brief-propagation into the work order gave the worker the fallback context.
- **T03** (README) cleared first try.
- **Terminal review**: passed. Mission `done`.
- Independent ground-truth check confirmed the artifact genuinely works (quoted-comma + empty-value handling, `--pretty`, runnable test file, documented README).

Cost: **~288 K tokens** total, dominated by Qwen3.6 **thinking mode** across ~12 agentic sessions. On self-served infra the token cost is ~free; wall-clock was the real cost. Confirms the standing optimization: **disable thinking for worker/validator roles, keep it for the orchestrator.**

Significance: every mechanism of the mission tier (constrained planning, bounded workers, dual independent validators, gates, regression-fed retry, attention→bounded-replan, terminal review, disk checkpointing) is now proven working **live, over the distributed-inference network, on the premium-tier model** — with the system self-correcting an imperfect plan without human intervention. The escalation ladder's top tier is validated in production conditions.
