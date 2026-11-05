#!/usr/bin/env bash
# Boot-time self-update for Job Hunter on the Raspberry Pi.
#
# Run by job-hunter-update.service before the app starts. Updates, in order:
#   1. the app (git pull latest main) + its Python dependencies
#   2. Ollama (official installer re-run = upgrade in place)
#   3. the OS packages (cadence controlled by OS_UPDATE below)
#
# Everything is best-effort: a failure in any step logs and continues, so a Pi
# with no/poor internet (e.g. just moved to a new place) still boots into the app
# with whatever is already installed. Must run as root (the service sets User=root).
set -u

# --- config (edit these or override via /etc/default/job-hunter) ---
APP_DIR="${APP_DIR:-__APP_DIR__}"
APP_USER="${APP_USER:-__USER__}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1}"
# OS_UPDATE: none | security | full
#   none     - never touch OS packages on boot (fastest, safest)
#   security - apt update + unattended security upgrades only (default)
#   full     - apt update + full-upgrade every boot (thorough, slower boots)
OS_UPDATE="${OS_UPDATE:-security}"
# ---------------------------------------------------------------

[ -f /etc/default/job-hunter ] && . /etc/default/job-hunter

log() { echo "[job-hunter-update] $*"; }

# 1. App: pull latest main + refresh venv deps (as the app user, not root).
if [ -d "$APP_DIR/.git" ]; then
  log "Updating app from git (latest main)..."
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch --quiet origin main \
    && sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard --quiet origin/main \
    && log "App updated to $(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse --short HEAD)" \
    || log "App git update failed — keeping current checkout."

  if [ -x "$APP_DIR/.venv/bin/pip" ]; then
    log "Updating Python dependencies..."
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade \
      -r "$APP_DIR/requirements.txt" || log "pip update failed — keeping current deps."
  fi
else
  log "No git checkout at $APP_DIR — skipping app update."
fi

# 2. Ollama: re-running the official installer upgrades it in place.
if command -v ollama >/dev/null 2>&1; then
  log "Updating Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh \
    && log "Ollama updated." || log "Ollama update failed — keeping current version."
  # Make sure the model is present (no-op if already pulled).
  sudo -u "$APP_USER" ollama pull "$OLLAMA_MODEL" >/dev/null 2>&1 \
    && log "Model $OLLAMA_MODEL present." || log "Could not pull $OLLAMA_MODEL (offline?)."
else
  log "Ollama not installed — skipping."
fi

# 3. OS packages.
case "$OS_UPDATE" in
  none)
    log "OS update: disabled (OS_UPDATE=none)."
    ;;
  full)
    log "OS update: full upgrade..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get -y -qq full-upgrade \
      && apt-get -y -qq autoremove || log "OS full-upgrade failed — continuing."
    ;;
  security|*)
    log "OS update: security upgrades..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq || log "apt update failed — continuing."
    if command -v unattended-upgrade >/dev/null 2>&1; then
      unattended-upgrade -v || log "unattended-upgrade failed — continuing."
    else
      log "unattended-upgrades not installed; skipping (install it or use OS_UPDATE=full)."
    fi
    ;;
esac

log "Update run complete."
exit 0
