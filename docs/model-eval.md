# Model evaluation — local candidates on M3 Pro 18 GB

*Date: 2026-07-03. Ollama 0.31.1 (upgraded from 0.22.1 mid-eval — the older build didn't know the `qwen35moe` architecture). GPU wired limit raised to 15360 MB via `sudo sysctl iogpu.wired_limit_mb=15360` so the 35B fits fully on GPU.*

Head-to-head of the concierge and workhorse candidates against the Phase 0/1 baseline, on the same rig.

## Candidates

| Model | Quant | On-disk | Role tested |
|---|---|---|---|
| Qwen3-4B | Q4 (Ollama default) | 2.5 GB | concierge |
| Qwen3.6-35B-A3B (35B total / 3B active MoE) | unsloth UD-Q2_K_XL | 13 GB | workhorse (worker + validator) |
| Qwen3-14B | Q4_K_M | 9.3 GB | baseline (Phase 0/1 workhorse) |

## Speed (M3 Pro, ~200 GB/s bandwidth, fully on GPU)

| Model | Warm generation | vs 14B |
|---|---|---|
| Qwen3-4B | **39 tok/s** | ~3–6× |
| Qwen3.6-35B-A3B UD-Q2 | **25 tok/s** | ~2–4× |
| Qwen3-14B | 6–13 tok/s | 1× |

The MoE thesis holds on speed: 3B active params → the 35B generates ~2–4× faster than the 14B dense **once it fits fully on GPU**. Before raising the wired limit it spilled 6% to CPU and ran erratically (2–9 tok/s) — the 13 GB model exceeds macOS's default ~11.7 GB GPU cap on an 18 GB machine.

## Concierge — Qwen3-4B: PASS

- 39 tok/s — feels instant, right for an always-resident router.
- Routing via constrained decoding: **5/5 correct** on a spread of greeting / memory-recall / single-task / multi-step-mission / cross-file-debug inputs, valid JSON every time.
- Confirms the design point: the concierge doesn't need strong *native* tool-calling because the routing schema is grammar-enforced.

**Verdict: Qwen3-4B is the concierge.**

## Workhorse — Qwen3.6-35B-A3B UD-Q2: FAST BUT FRAGILE AT Q2

The 35B is faster and far more capable on paper (SWE-bench ~69–73% vs the 14B), but the **UD-Q2 quant needed to fit 18 GB degrades agentic reliability** in ways that matter:

- **As a worker (happy path): works.** Built `hello.py`, ran it, confirmed, `done=true` in 3 clean turns.
- **As a validator: fragile.**
  - Against the known-broken build it returned the right top-line verdict (fail) — but its only tool call was `ls -la /workdir/`, a **hallucinated absolute path** that doesn't exist. It never ran the actual checks; the "correct" verdict was luck, not evidence.
  - It **ignored an explicit instruction** ("use paths relative to the work directory, do not invent /workdir") added to the order — and did the exact forbidden thing anyway.
  - Against the **good build it returned a false negative** (`overall_passed: false` on correct code) with malformed output (duplicate item id, empty evidence).
  - Root cause: when its first exploratory command failed, it **gave up instead of recovering** — no retry, no alternative. The worker never hit this because its first action happened to succeed.

By contrast the 14B at Q4, in the same validator role (Phase 1), actually ran pytest and the CLI and pasted verbatim evidence.

**Interpretation:** the failures are consistent with aggressive 2-bit quantization damaging instruction-adherence and error-recovery — the exact traits the honesty-critical validator role depends on. The 35B is not *unusable* at Q2 (it does happy-path work fast), but it is **not reliably better than the 14B for validation on this hardware**, which is the role that guards correctness.

## Conclusions

1. **Concierge: Qwen3-4B — adopted.**
2. **On an 18 GB machine, the 14B (Q4) remains the more trustworthy workhorse for the validator role**, despite being 2–4× slower. Robustness beats speed for the role that decides "is this actually done."
3. **The 35B-A3B is really a Q4 / 24 GB+ model.** UD-Q2 is the only quant that fits 18 GB and the quant is what breaks it. It should be re-evaluated at Q4_K_M (~22 GB) on the RTX 3090, where it's expected to dominate — and it fits the design's tiering exactly: the 35B is the *bigger-node / premium* model, not the small-node workhorse. This 18 GB laptop is a small node.
4. **Two cheap kernel hardenings worth doing regardless** (they'd help every model): teach profiles their working directory explicitly in the order (added, but Q2 ignored it — higher-quant models will benefit), and add a validator-prompt rule to recover from a failed command (try an alternative, never conclude from a single failed exploration).

## Operational notes

- The `sudo sysctl iogpu.wired_limit_mb=15360` setting resets on reboot; re-apply to run the 35B on this machine.
- The failed Q3_K_M pull (wrong tag; real name is `UD-Q3_K_M`) left ~16 GB of orphaned blobs in `~/.ollama`; not reclaimed (no `ollama` prune command; manual blob deletion is risky).
