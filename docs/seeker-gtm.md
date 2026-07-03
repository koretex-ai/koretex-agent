# Solana Seeker as a distribution channel

*Researched 2026-07-03. Decision-oriented summary for how much to invest in the Solana Seeker as a go-to-market channel for koretex-agent. Sources cited inline; figures verified against primary/official sources (an automated research pass produced the source set, then key facts were re-checked directly).*

## The one-line decision

**Ship the Seeker as a consumer + wallet-onboarding channel, not as a serving node.** The device runs only the tiny on-device concierge and escalates everything else to the Koretex network — which its mid-range hardware handles fine. The provider-node / earning story belongs to capable hardware (desktop, GPU box), never the phone.

## Hardware (confirmed)

| Spec | Value |
|---|---|
| SoC | MediaTek Dimensity 7300 (octa-core, mid-range) |
| RAM | **8 GB** |
| Storage | 128 GB (UFS 3.1) |
| Display | 6.36" AMOLED, 2670×1200, 120 Hz |
| Battery | 4,500 mAh, wireless charging |
| OS | Android 15 |
| Price | $500 ($450 early pre-order) |

Sources: [solanamobile.com/seeker](https://solanamobile.com/seeker) (official), [Decrypt review](https://decrypt.co/336582/solana-seeker-review-more-measured-crypto-phone), [CoinDesk](https://www.coindesk.com/tech/2025/08/06/solana-s-seeker-phone-fixes-saga-s-flaws-with-usability-upgrade).

**Performance note:** the Dimensity 7300 is a *downgrade* from the Saga's Snapdragon 8 Gen 1 — reviewers measured ~33% lower multi-core and ~44% lower single-core, roughly Samsung A56 tier ([Decrypt](https://decrypt.co/336582/solana-seeker-review-more-measured-crypto-phone)). Fine for a phone; it just reinforces that heavy inference must be remote.

## On-device LLM feasibility

- **No vendor NPU path.** The 7300 has a MediaTek APU, but MediaTek's on-device-LLM acceleration (NeuroPilot / official Llama support) targets the 8300/9300/9400 — not the 7300 ([MediaTek](https://www.mediatek.com/tek-talk-blogs/mediatek-dimensity-supports-meta-llama-3.2)). Realistic inference is **CPU or Mali-GPU via llama.cpp / MLC**, at modest tok/s.
- **A 1.7B–4B Q4 concierge runs acceptably** for routing + short replies — which is all tier-0 ever does. Heavier work escalates to the network, so the weak SoC is a non-issue by design.
- **Sizing:** prefer **Qwen3-1.7B Q4 (~1.1 GB) resident**, load 4B (~2.5 GB) on demand — 4B won't stay pinned on 8 GB under memory pressure.
- **Precedent:** [SeekerClaw](https://www.blockhead.co/2026/03/09/seekerclaw-brings-24-7-ai-agents-to-the-solana-seeker-phone/) runs 24/7 AI agents on the Seeker but via **cloud Claude API, not on-device** — the flagship AI-agent-on-Seeker project chose the network path, validating concierge-local + escalate-to-network.

## Distribution / GTM surface (the strong part)

- **~150,000 units shipped across 57 countries**, vs ~20,000 Saga ever made — ~7.5× the install base, all self-selected crypto-native users ([Decrypt](https://decrypt.co/336582/solana-seeker-review-more-measured-crypto-phone), [Cryptorank](https://cryptorank.io/news/feed/1af7c-solana-mobile-s-seeker-smartphone-pre-orders-surpass-150-000-in-57-countries)).
- **Native Seed Vault + wallet on every device:** hardware-isolated key storage (TEE-backed; *not* a confirmed discrete secure-element chip — Saga used ARM TrustZone), fingerprint + double-tap signing. This matches koretex's wallet-identity model — the device ships with exactly the wallet primitive the client needs.
- **dApp Store:** alternative Android store, no Google 30% cut, curated for Solana apps — but thin on apps beyond trading (Jupiter/Drift/etc.). Small ecosystem = **low competition** for a polished wallet-native AI agent.

## Risks / limitations for our use case

- **Not a serving node.** An 8 GB mid-range phone can barely run a 4B locally, cannot serve the 15B brain, and sustained/background serving would wreck battery and thermals and fight Android background-execution limits. Mobile is consumer-only. *(Not a problem — the design never asked the phone to serve.)*
- **Always-resident memory pressure:** use 1.7B resident, 4B on-demand.
- **Unmeasured on the 7300:** the tok/s expectations are extrapolated from comparable SoCs. **Before committing budget, benchmark Qwen3-1.7B and 4B Q4 on an actual Seeker** (tok/s, battery drain, memory headroom with the wallet app running).

## GTM messaging guardrail

Keep the two value props separate:
- **Phone = smart, wallet-native client** (free local tier-0, network for real work).
- **Desktop / GPU box = the earning provider node.**

Do not tell Seeker users they earn by serving — they won't, and were never meant to.
