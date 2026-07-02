# Phase 0 findings — validating the premises

*Test machine: MacBook Pro M3 Pro, 18 GB unified memory. Date: 2026-07-02. Status: in progress.*

Phase 0 asks one question: **can a local ~14B open model hold the agent roles our architecture needs?** No new product code — we run the existing pieces (Hermes, Zenith, Ollama, the live Koretex dispatcher) and measure.

## Environment notes

- This machine is already a **live Koretex provider node**: the koretex-node managed engine (`~/.koretex/engine/ollama`, port 11435) serves the network, while the user's own Homebrew Ollama runs on 11434. Hermes was already configured to consume through `https://dispatcher.koretex.ai/v1` — so the serve-and-consume loop existed here before Phase 0 started. Phase 0 is purely about local-model capability.
- Test model: `qwen3:14b` (Q4_K_M, 9.3 GB) on the Homebrew Ollama, restarted with:
  `OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`
  Resident footprint at 32K context: **12 GB, 100% GPU** — leaves ~6 GB for the OS on an 18 GB machine. Two models cannot be resident at once on this hardware (if the network sends a job to the koretex engine while testing, they contend).

## What worked

| Test | Result |
|---|---|
| Raw tool calling (OpenAI-compatible API, one shell tool) | ✅ Well-formed `tool_calls` JSON on the first attempt. Warm single-turn latency ~1.5 s |
| Hermes full agentic loop (`hermes -z`, create file → read back → report) | ✅ Correct tool sequence, correct final answer (even verified byte count). **But: 4 m 18 s wall-clock for a two-tool-call task** |
| Zenith install + `init --agent hermes` | ✅ Installs clean (Python 3.14 venv); stages `.mcp.json`, orchestrator prompt, and the bundled skills for the Hermes provider |
| Zenith mission on all-Hermes roles (orchestrator/worker/validator on qwen3:14b) | ⏳ Mission 1 (`csv2json` CLI + pytest suite) in flight — results to be appended |

## The context-size problem, quantified

Two findings that directly shape the Hermes-lite fork:

1. **Hermes hard-refuses models under 64K context.** `MINIMUM_CONTEXT_LENGTH = 64_000` in `agent/model_metadata.py` aborts the session. For Phase 0 we patched the installed Hermes to a 32K floor (local commit `146b898` in `~/.hermes/hermes-agent`, cleanly revertible). The fork must make this floor configurable and scale compression thresholds accordingly.
2. **Hermes's fixed prompt overhead is ~97 KB ≈ 20–25K tokens** (`hermes prompt-size`, platform=cli):
   - System prompt: 39.9 KB (21 KB stable identity/guidance/skills-index + 18 KB cwd context files + volatile)
   - **Tool schemas: 58.4 KB across 35 tools**
   - On a 32K-token window this consumes ~70–80% before the user says a word. It is also the main driver of the 4-minute smoke test: every turn re-prefits a huge prefix through a 14B at laptop prefill speeds.

**Implication:** the biggest single lever for local-model viability is not the model — it's cutting the prompt+schema prefix. A ~12-tool Hermes-lite with compressed schemas and an on-demand skills index should bring the fixed prefix to ~4–6K tokens, leaving ~26K of a 32K window for actual work, and cutting per-turn prefill time roughly 4–5×.

## Deviations / setup friction (for the installer to absorb later)

- `zenith init` must run from a Zenith **source checkout** (it derives its MCP server command from the repo root) — `pip install` alone isn't enough. The pip package also lives in a nested `zenith/zenith/` directory.
- Hermes config has no CLI override for `base_url`; switching between the dispatcher and local Ollama means editing `~/.hermes/config.yaml` (backup kept at `config.yaml.bak.koretex-dispatcher`). The fork should make endpoint switching first-class.
- Ollama env (context length, KV quantization) requires restarting the server process; the Homebrew service doesn't carry env vars by default.

## Mission 1: an accidental — and valuable — naive baseline

Mission 1 (build `csv2json` + pytest suite, "complete when all tests pass") ran **without Zenith tools** due to a wiring gap discovered mid-run: Hermes does not auto-discover a workspace `.mcp.json` (that is a Claude Code convention; Hermes uses its own per-profile registry via `hermes mcp add`). Zenith's `init --agent hermes` stages `.mcp.json` and tells you to "just start hermes" — advertised integration, but the last mile is missing. The fix was one command (`hermes mcp add zenith --command uv --env ... --args run --project <zenith-src> zenith-server --mode orchestrator`), which connected and enabled all 7 orchestrator tools.

So mission 1 became the **harness-free control**: qwen3:14b, given the same brief, just built the tool directly. Result after ~75 minutes (still running when stopped):

- `cli.py`: plausible and essentially correct (argparse, csv.DictReader, --pretty, error handling)
- `tests/test_csv2json.py`: **SyntaxError — unterminated triple-quoted string. The test suite could not even be collected.**
- The agent did not catch this. Its own explicit acceptance criterion ("all tests pass") was unmet for over an hour with no self-correction.

This is precisely the small-model failure mode the architecture bets on: **plausible work + confident non-verification.** The mission-2 rerun (same brief, Zenith properly wired) measures whether independent validators catch what the naive loop missed.

## Mission 2 measurements (Zenith wired; to append on completion)

- [ ] Did the orchestrator follow the Zenith MCP protocol (start_project → submit_plan → advance/decide loops)?
- [ ] JSON handoff validity rate (WorkHandoff/ValidateHandoff)
- [ ] Validator behavior: rubber-stamp vs genuine catches (does it catch a broken test suite?)
- [ ] Premature-completion catches (terminal review)
- [ ] Wall-clock and token totals per mission
