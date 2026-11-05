#!/usr/bin/env bash
# One-shot setup for Job Hunter on a fresh Raspberry Pi (Raspberry Pi OS Bookworm+).
#
# After this runs once, the Pi will — on every boot, with no manual steps —
# update the OS/app/Ollama, start Ollama, and start the Job Hunter web app,
# reachable on the LAN at http://<pi-ip>:8000.
#
# Usage (on the Pi):
#   git clone https://github.com/ounis/job.git ~/job
#   cd ~/job
#   sudo ./deploy/setup.sh
#
# Idempotent: safe to re-run (e.g. after changing config). Requires sudo/root.
set -euo pipefail

# --- config ---
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1}"
APP_PORT="${APP_PORT:-8000}"
# OS update cadence on boot: none | security | full  (see deploy/update.sh)
OS_UPDATE="${OS_UPDATE:-security}"
# --------------

log() { echo -e "\n=== $* ==="; }
die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Please run with sudo: sudo ./deploy/setup.sh"

# The app dir is this script's repo root; the owning user is whoever cloned it
# (the invoking sudo user), not root.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
APP_GROUP="$(id -gn "$APP_USER")"
echo "App dir : $APP_DIR"
echo "App user: $APP_USER ($APP_GROUP)"
echo "Model   : $OLLAMA_MODEL   Port: $APP_PORT   OS updates: $OS_UPDATE"

log "Installing OS dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  git python3 python3-venv python3-pip curl ca-certificates \
  network-manager unattended-upgrades

log "Installing Ollama"
if command -v ollama >/dev/null 2>&1; then
  echo "Ollama already installed ($(ollama --version 2>/dev/null | head -1))."
else
  curl -fsSL https://ollama.com/install.sh | sh
fi
# The Ollama installer sets up and enables its own systemd service (ollama.service).
systemctl enable --now ollama.service 2>/dev/null || true

log "Pulling Ollama model: $OLLAMA_MODEL (this can take a while)"
sudo -u "$APP_USER" ollama pull "$OLLAMA_MODEL" || \
  echo "WARN: model pull failed (continue; boot-time update will retry)."

log "Creating Python virtualenv + installing app dependencies"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

log "Seeding .env if missing"
if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  # Default a fresh Pi install to local Ollama (free, offline-capable AI).
  sudo -u "$APP_USER" sed -i 's/^AI_PROVIDER=.*/AI_PROVIDER=ollama/' "$APP_DIR/.env" || true
  echo "Created .env from .env.example (AI_PROVIDER=ollama). Edit it to add API keys."
else
  echo ".env already exists — left untouched."
fi

log "Writing /etc/default/job-hunter (runtime config for the updater)"
cat >/etc/default/job-hunter <<EOF
# Config for job-hunter-update.service (deploy/update.sh). Edit + reboot to apply.
APP_DIR=$APP_DIR
APP_USER=$APP_USER
OLLAMA_MODEL=$OLLAMA_MODEL
# OS_UPDATE: none | security | full
OS_UPDATE=$OS_UPDATE
EOF

log "Installing systemd services"
# Substitute the real user/paths into the unit templates, then install them.
for unit in job-hunter.service job-hunter-update.service; do
  sed -e "s#__APP_DIR__#$APP_DIR#g" \
      -e "s#__USER__#$APP_USER#g" \
      "$APP_DIR/deploy/$unit" > "/etc/systemd/system/$unit"
done
# Make the scripts executable.
chmod +x "$APP_DIR/deploy/update.sh"

# Apply the configured port to the app service.
sed -i "s/^Environment=PORT=.*/Environment=PORT=$APP_PORT/" \
  /etc/systemd/system/job-hunter.service

systemctl daemon-reload
systemctl enable job-hunter-update.service job-hunter.service
systemctl restart job-hunter.service

# Figure out the LAN IP for the closing message.
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "Done"
cat <<EOF

Job Hunter is installed and will start automatically on every boot.

  Web UI:   http://${IP:-<pi-ip>}:$APP_PORT
  WiFi setup page (to move the Pi to a new network): http://${IP:-<pi-ip>}:$APP_PORT/wifi

On each boot the Pi will (best-effort) update the app, Ollama, and OS packages
(OS_UPDATE=$OS_UPDATE), then start Ollama and the app. No manual steps needed.

Useful commands:
  sudo systemctl status job-hunter          # app status
  journalctl -u job-hunter -f               # app logs
  journalctl -u job-hunter-update -b         # last boot's update log
  sudo systemctl restart job-hunter          # restart the app

To change OS update behavior: edit /etc/default/job-hunter (OS_UPDATE=...) and reboot.
EOF
