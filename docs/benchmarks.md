# Benchmarks & test protocol

Record of the tests used to validate the step-1 (thinking-off) and step-1b
(worker-stops-when-done) changes, written so they can be **repeated a few times**
to confirm the results are consistent (single runs have real run-to-run
variance — see step 1 in NEXT-STEPS, where a strong single-call number did not
predict the mission total).

All measurements are on the premium tier: **Qwen3.6-35B-A3B Q4** served from the
RTX 3090, consumed over the Koretex dispatcher (`https://dispatcher.koretex.ai/v1`).
Before any network run, confirm the node is up:

```bash
curl -s https://dispatcher.koretex.ai/healthz   # want ok:true, nodes>=1, llama.cpp backend
```

## The three tests

### T1 — Unit + budget gate (fast, deterministic)
```bash
phase0/.venv/bin/python -m pytest tests/ -q
```
Expected: **14 passing**. Includes the per-profile prefix-budget gate and the
thinking-policy tests. Deterministic — no variance; run once per code change.

### T2 — Single-worker probe (targeted, ~1–2 min)
One worker session on the T01 csv2json contract, 35B via dispatcher. Measures
whether the worker **stops when done** instead of running to `max_turns`, and its
token cost. Driven directly via the API (not the CLI) to avoid `--assert`
pipe-splitting on the assertion strings. Script: `scripts/worker-probe.py`.
```bash
KORETEX_API_KEY=$(python3 -c "import json;print(json.load(open('$HOME/.koretex/customer.json'))['key'])") \
KORETEX_AGENT_BASE_URL=https://dispatcher.koretex.ai/v1 \
KORETEX_AGENT_MODEL=hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M \
phase0/.venv/bin/python scripts/worker-probe.py
```
Report: `turns`, `prompt_tokens`, `completion_tokens`, `done`.

### T3 — Full mission (end-to-end, ~10–15 min)
The whole csv2json mission (plan → 3 tasks × worker+2 validators → terminal
review) into a fresh workdir. This is the number that matters — it's what the
network actually pays.
```bash
nohup bash scripts/bench-mission.sh <label> > phase0/bench/<label>.log 2>&1 & disown
# when done:
python3 scripts/bench-report.py phase0/bench/<label>
```
Report (from `bench-report.py`): total tokens (prompt/completion), terminal
review pass/fail, per-task attempts, and per-session turn counts with any
**maxed-out** sessions flagged.

## How to repeat (consistency check)

Run T3 with fresh labels `m1`, `m2`, `m3`, … (each gets its own workdir, so runs
don't resume each other). Fill in the table below. We expect: review passes
every time, 0 sessions maxed out, and total tokens clustering — not a wild swing.

## Results log

### Reference baselines (before the changes)
| test | metric | value | notes |
|------|--------|-------|-------|
| T3 baseline (thinking ON, pre-1b) | total tokens | 287,618 | prompt 220,629 + completion 66,989; the "~288K" run |
| T3 baseline | first worker (T01) session | 20 turns / 85,453 tok | maxed out; finished real work by turn 13, then ceremonial re-verify |
| T2 baseline (thinking ON) worker probe | — | 20 turns / 85,453 tok | same maxed-out worker |

### After step 1 (thinking OFF) — 2026-07-03
| test | metric | value | notes |
|------|--------|-------|-------|
| T3 (thinking OFF, pre-1b) | total tokens | 284,020 | completion −68% (→21,650), total flat; prompt rose (turn-count variance) |
| T3 | review | PASS 8/8 | 0 retries |

### After step 1b (worker stops when done) — 2026-07-03
| test | metric | value | notes |
|------|--------|-------|-------|
| T1 | pytest | 14 passing | |
| T2 worker probe | — | 8 turns / 16,111 tok | −81% vs baseline worker; done=true, work correct |
| T3 `m1` | total tokens | **415,050** | prompt 375,410 + completion 39,640; review PASS; **WORSE than 288K baseline** |
| T3 `m1` | worker turns | [20, 7, 20, 10] | **2 of 4 workers still MAXED at 20** — 1b prompt fix ignored half the time |
| T3 `m1` | maxed-out sessions | 4 / 13 | 2 workers + 2 validators |
| T3 `m1` | T03 attempts | 2 | a validation retry (stochastic) spun an extra worker+validator round |

**Finding from m1 (2026-07-03) — root-caused, and it changes the conclusion.** Investigated the two maxed-out workers' trajectories. They were NOT doing ceremonial re-verification (the 1b target); they were honestly stuck fighting a **broken contract assertion** the orchestrator emitted for VAL-006:
`[ -f README.md ] && grep -q 'usage' README.md && grep -q '\-\-pretty' README.md`
- `grep -q 'usage'` is **case-sensitive** → fails on the "Usage" heading a correct README uses.
- `grep -q '\-\-pretty'` has pointless/confusing escaping that sent the worker into a multi-turn debug spiral.
The deliverable was correct throughout (README has usage + --pretty; review passed 8/8); the **contract was wrong**. Worker #2 also thrashed re-writing near-identical README content (a model misperception under stress — `write_file` is fine, no idempotency skip).

**Revised conclusions (supersede the earlier "build a turn-cap backstop" note):**
1. The 1b prompt fix helps genuine ceremonial cases (T2: 8 turns) but is irrelevant to these blowups — the workers weren't "done" by the broken contract's definition.
2. A structural turn-cap backstop would be **harmful here** — it would truncate a worker legitimately struggling and mask the real bug (bad contract) behind a clean cap. Do NOT build it as the primary fix.
3. **Real root cause = orchestrator-generated fragile assertions → this is step 2 (deterministic plan-lint).** Concrete lint rules this surfaced: reject case-sensitive prose greps on docs (require `grep -qi` or ban documentation-wording gates), reject weird escaping like `'\-\-'`, and generally don't gate on README prose content.
4. Secondary: give workers an escape hatch — when the artifact is honestly produced but an assertion still fails in a way that looks like the *assertion* is wrong, `request_attention=true` instead of thrashing to `max_turns`.
5. Mission total swung 284K → 415K largely because of this stuck-worker spiral + a stochastic T03 retry — confirming single runs prove nothing at mission level (still do the repeats, but after step 2).

<!-- Append m2, m3, ... rows here for the consistency repeats (after the structural backstop lands). -->
