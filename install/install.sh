#!/usr/bin/env bash
# Koretex Agent — consumer installer (curl | bash).
#
# Installs the CONSUMER face only: the tier-0 concierge running LOCALLY on a
# bundled llama.cpp server, with real work dispatched to the Koretex NETWORK.
# This runs on any machine — the network decouples agent quality from local
# hardware, so a laptop gets 35B-quality work by consuming from the network and
# paying credits. The PROVIDER face (serving the 35B, earning) is a SEPARATE
# installer in the koretex-node repo; the two share only the wallet/account.
#
# Usage:
#   curl -fsSL https://get.koretex.ai/install.sh | bash
#   curl -fsSL https://get.koretex.ai/install.sh | bash -s -- --key <API_KEY>
#   ./install.sh --dry-run          # validate the flow without downloading
set -euo pipefail

# ── configurable knobs (maintainer confirms exact asset URLs) ───────────────
KORETEX_HOME="${KORETEX_HOME:-$HOME/.koretex-agent}"
DISPATCHER_URL="${DISPATCHER_URL:-https://dispatcher.koretex.ai/v1}"
WORK_MODEL="${WORK_MODEL:-hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M}"   # network work tier
CONCIERGE_PORT="${CONCIERGE_PORT:-8080}"
CONCIERGE_MODEL_NAME="${CONCIERGE_MODEL_NAME:-qwen3-4b}"                    # served name
LLAMACPP_REPO="${LLAMACPP_REPO:-ggml-org/llama.cpp}"                       # moved from ggerganov/
LLAMACPP_RELEASE="${LLAMACPP_RELEASE:-b9870}"                              # pinned; override to bump
CONCIERGE_GGUF_URL="${CONCIERGE_GGUF_URL:-https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf}"
AGENT_VERSION="${AGENT_VERSION:-0.1.1}"
AGENT_PKG="${AGENT_PKG:-https://github.com/koretex-ai/koretex-agent/releases/download/v${AGENT_VERSION}/koretex_agent-${AGENT_VERSION}-py3-none-any.whl}"

DRY_RUN=0
API_KEY="${KORETEX_API_KEY:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --key) API_KEY="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
run()  { if [ "$DRY_RUN" = 1 ]; then echo "   [dry-run] $*"; else eval "$*"; fi; }

# ── 1. platform detection ───────────────────────────────────────────────────
detect_platform() {
  local os arch
  os="$(uname -s)"; arch="$(uname -m)"
  case "$os" in
    Darwin) OS=macos ;;
    Linux)  OS=linux ;;
    *) echo "unsupported OS: $os (Windows consumer ships with the desktop app)" >&2; exit 1 ;;
  esac
  case "$arch" in
    arm64|aarch64) ARCH=arm64 ;;
    x86_64|amd64)  ARCH=x64 ;;
    *) echo "unsupported arch: $arch" >&2; exit 1 ;;
  esac
  # llama.cpp names macOS assets "macos" and Linux assets "ubuntu"; both .tar.gz.
  local asset_os; [ "$OS" = macos ] && asset_os=macos || asset_os=ubuntu
  LLAMACPP_ASSET="llama-${LLAMACPP_RELEASE}-bin-${asset_os}-${ARCH}.tar.gz"
  log "platform: $OS/$ARCH  (asset: $LLAMACPP_ASSET)"
}

# ── 2. dirs + python agent (its own venv, no system pollution) ──────────────
install_agent() {
  log "installing agent into $KORETEX_HOME"
  run "mkdir -p '$KORETEX_HOME'/{runtime,models,bin}"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required (bundled interpreter comes with the desktop app)" >&2; exit 1
  fi
  run "python3 -m venv '$KORETEX_HOME/venv'"
  run "'$KORETEX_HOME/venv/bin/pip' install --quiet --upgrade pip"
  run "'$KORETEX_HOME/venv/bin/pip' install --quiet '$AGENT_PKG'"
}

# ── 3. bundled llama.cpp runtime (the local concierge server) ───────────────
fetch_runtime() {
  log "fetching llama.cpp runtime ($LLAMACPP_ASSET)"
  local url="https://github.com/${LLAMACPP_REPO}/releases/download/${LLAMACPP_RELEASE}/${LLAMACPP_ASSET}"
  run "curl -fSL '$url' -o '$KORETEX_HOME/runtime/llama.tar.gz'"
  run "tar -xzf '$KORETEX_HOME/runtime/llama.tar.gz' -C '$KORETEX_HOME/runtime'"
  run "rm -f '$KORETEX_HOME/runtime/llama.tar.gz'"
  # the tarball nests the binaries (e.g. build/bin/llama-server); expose one path
  run "ln -sf \"\$(find '$KORETEX_HOME/runtime' -name llama-server -type f | head -1)\" '$KORETEX_HOME/runtime/llama-server'"
}

# ── 4. concierge model (Qwen3-4B, ~2.5GB) ───────────────────────────────────
fetch_model() {
  log "fetching concierge model (Qwen3-4B gguf, ~2.5GB)"
  run "curl -fSL '$CONCIERGE_GGUF_URL' -o '$KORETEX_HOME/models/${CONCIERGE_MODEL_NAME}.gguf'"
}

# ── 5. config: local concierge + network work tier ──────────────────────────
write_config() {
  log "writing config"
  if [ -z "$API_KEY" ] && [ "$DRY_RUN" != 1 ]; then
    printf 'Koretex API key (from your account at koretex.ai): '
    read -r API_KEY </dev/tty
  fi
  run "cat > '$KORETEX_HOME/config.env' <<CFG
# Work tier → the Koretex network (billed to credits)
KORETEX_AGENT_BASE_URL=$DISPATCHER_URL
KORETEX_AGENT_MODEL=$WORK_MODEL
KORETEX_API_KEY=${API_KEY:-REPLACE_ME}
# Concierge tier → local bundled llama.cpp server (free, on-device)
KORETEX_CONCIERGE_BASE_URL=http://localhost:$CONCIERGE_PORT/v1
KORETEX_CONCIERGE_MODEL=$CONCIERGE_MODEL_NAME
KORETEX_CONCIERGE_API_KEY=local
CFG"
}

# ── 6. keep the concierge server resident (launchd / systemd) ───────────────
install_service() {
  log "installing local concierge service"
  local serve="'$KORETEX_HOME/runtime/llama-server' -m '$KORETEX_HOME/models/${CONCIERGE_MODEL_NAME}.gguf' --port $CONCIERGE_PORT --alias $CONCIERGE_MODEL_NAME"
  if [ "$OS" = macos ]; then
    local plist="$HOME/Library/LaunchAgents/ai.koretex.concierge.plist"
    run "cat > '$plist' <<PL
<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<plist version=\"1.0\"><dict>
  <key>Label</key><string>ai.koretex.concierge</string>
  <key>ProgramArguments</key><array>
    <string>$KORETEX_HOME/runtime/llama-server</string>
    <string>-m</string><string>$KORETEX_HOME/models/${CONCIERGE_MODEL_NAME}.gguf</string>
    <string>--port</string><string>$CONCIERGE_PORT</string>
    <string>--alias</string><string>$CONCIERGE_MODEL_NAME</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
PL"
    run "launchctl unload '$plist' 2>/dev/null || true"
    run "launchctl load '$plist'"
  else
    local unit="$HOME/.config/systemd/user/koretex-concierge.service"
    run "mkdir -p '$HOME/.config/systemd/user'"
    run "cat > '$unit' <<UNIT
[Unit]
Description=Koretex local concierge (llama.cpp)
[Service]
ExecStart=$serve
Restart=always
[Install]
WantedBy=default.target
UNIT"
    run "systemctl --user daemon-reload && systemctl --user enable --now koretex-concierge.service"
  fi
}

# ── 7. launcher on PATH ─────────────────────────────────────────────────────
# Named `koretex-agent`, NOT `koretex` — the latter belongs to the koretex-node
# CLI (separate installer). Two products, two commands.
install_launcher() {
  log "installing 'koretex-agent' launcher"
  if command -v koretex-agent >/dev/null 2>&1 && [ "$(readlink "$(command -v koretex-agent)")" != "$KORETEX_HOME/bin/koretex-agent" ]; then
    echo "   note: a 'koretex-agent' already exists at $(command -v koretex-agent); it will be shadowed by this install's PATH entry" >&2
  fi
  run "cat > '$KORETEX_HOME/bin/koretex-agent' <<'LAUNCH'
#!/usr/bin/env bash
set -a; . \"\$HOME/.koretex-agent/config.env\"; set +a
exec \"\$HOME/.koretex-agent/venv/bin/koretex-agent\" concierge --task \"\$*\" --workdir \"\$(pwd)\"
LAUNCH"
  run "chmod +x '$KORETEX_HOME/bin/koretex-agent'"
  local dest="/usr/local/bin/koretex-agent"
  if [ -w "$(dirname "$dest")" ] 2>/dev/null; then run "ln -sf '$KORETEX_HOME/bin/koretex-agent' '$dest'"
  else run "mkdir -p '$HOME/.local/bin' && ln -sf '$KORETEX_HOME/bin/koretex-agent' '$HOME/.local/bin/koretex-agent'"; fi
}

# ── 8. verify ───────────────────────────────────────────────────────────────
verify() {
  log "verifying"
  if [ "$DRY_RUN" = 1 ]; then log "dry-run complete — flow validated, no downloads performed"; return; fi
  # concierge answers a trivial query locally (no network spend)
  run "'$KORETEX_HOME/bin/koretex-agent' 'what is 2+2?' | tail -5 || true"
  log "done. Try:  koretex-agent \"create a hello.py that prints hello\""
}

detect_platform
install_agent
fetch_runtime
fetch_model
write_config
install_service
install_launcher
verify
