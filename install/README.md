# Koretex Agent — consumer installer

`install.sh` sets up the **consumer face**: the tier-0 concierge running locally
on a bundled llama.cpp server, with real work dispatched to the Koretex network.
It runs on any machine — the network decouples agent quality from local hardware.

The **provider face** (serving the 35B, earning credits) is a **separate
installer** in the [`koretex-node`](https://github.com/koretex-ai/koretex-node)
repo (24GB+ boxes only; 35B-or-nothing). The two share only the wallet/account —
kept deliberately un-commingled.

## Usage

```bash
curl -fsSL https://get.koretex.ai/install.sh | bash                    # prompts for API key
curl -fsSL https://get.koretex.ai/install.sh | bash -s -- --key <KEY>  # non-interactive
./install.sh --dry-run                                                 # validate flow, no downloads
```

## What it does

1. Detect platform (macOS/Linux, arm64/x64).
2. Install the agent into its own venv under `~/.koretex-agent/` (no system pollution).
3. Fetch the bundled **llama.cpp** runtime (the local concierge server).
4. Fetch the **Qwen3-4B** gguf (~2.5GB) — the concierge model.
5. Write `config.env`: **work tier → dispatcher** (billed to credits), **concierge tier → local llama.cpp** (free, on-device).
6. Install a **launchd/systemd service** so the concierge server stays resident.
7. Install a `koretex` launcher on PATH.
8. Verify with a trivial local query (no network spend).

## Topology it configures (the two-client concierge)

| Tier | Runs on | Env |
|---|---|---|
| Concierge (routing, chat, memory) | **local** llama.cpp server | `KORETEX_CONCIERGE_{BASE_URL,MODEL,API_KEY}` |
| Work (task / mission) | **network** dispatcher | `KORETEX_AGENT_{BASE_URL,MODEL}`, `KORETEX_API_KEY` |

The concierge answers cheap requests on-device for free; only real work is sent
to the network and billed. See `koretex_agent.client.concierge_client_from_env`.

## Before going live — maintainer must confirm (marked `TODO` in the script)

- **`LLAMACPP_RELEASE` + asset naming** — pin an exact llama.cpp release and verify the `llama-<tag>-bin-<os>-<arch>.zip` asset names (they vary by release).
- **`CONCIERGE_GGUF_URL`** — pin the exact Qwen3-4B gguf (repo + quant + revision).
- **`AGENT_PKG`** — publish a wheel / tag instead of `git+https` for reproducible installs.
- **Wallet/key provisioning** — the installer accepts an existing `--key`; the account-creation + buy-credits + balance-in-status-line flow is a separate backend concern (not yet wired here).

## Not yet validated on a real machine

The `--dry-run` validates the full control flow (platform detection, config,
service + launcher generation). The multi-GB downloads (llama.cpp binary, gguf)
and the launchd/systemd registration need a real target machine to validate
end-to-end — do this before publishing to `get.koretex.ai`.

## Later

- **Desktop app** (native/Electron) — the non-technical distribution; the Seeker mobile app is its sibling. Both wrap this same consumer component.
- **Windows** consumer — ships with the desktop app (bundled interpreter + runtime).
