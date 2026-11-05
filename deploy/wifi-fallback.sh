#!/usr/bin/env bash
# WiFi fallback hotspot for the Job Hunter Pi.
#
# Problem it solves: when the Pi is moved to a place with an UNKNOWN WiFi, it
# can't join any saved network, so it never gets an IP and the /wifi page is
# unreachable — a chicken-and-egg. This script detects "no WiFi after boot" and
# brings up the Pi's own hotspot so you can connect from a phone and configure
# the new network at http://10.42.0.1:8000/wifi (NetworkManager's AP gateway).
#
# Behavior (loop, run by wifi-fallback.service):
#   - Wait briefly for a normal (client) WiFi connection after boot.
#   - If still not connected, start a NetworkManager AP hotspot on wlan0.
#   - While the hotspot is up, keep checking whether a saved client network has
#     come back in range (or the user picked one via /wifi, which activates a
#     client profile) — if so, tear the hotspot down and let the client connect.
#
# Config via /etc/default/job-hunter-wifi (all optional):
#   HOTSPOT_SSID, HOTSPOT_PASSWORD, WIFI_DEV, WAIT_SECS, RECHECK_SECS
set -u

HOTSPOT_SSID="${HOTSPOT_SSID:-JobHunter-Setup}"
HOTSPOT_PASSWORD="${HOTSPOT_PASSWORD:-jobhunter}"   # WPA2 requires >= 8 chars
WIFI_DEV="${WIFI_DEV:-wlan0}"
WAIT_SECS="${WAIT_SECS:-60}"        # grace period to join a known net after boot
RECHECK_SECS="${RECHECK_SECS:-20}"  # while hotspot up, how often to retry clients
HOTSPOT_CON="jobhunter-hotspot"     # NM connection profile name for the AP

[ -f /etc/default/job-hunter-wifi ] && . /etc/default/job-hunter-wifi

log() { echo "[wifi-fallback] $*"; }

# True if wlan0 currently has a normal (client/station) connection — i.e. it's
# joined a real WiFi, NOT running our hotspot.
wifi_client_connected() {
  local state ssid mode
  state="$(nmcli -t -f GENERAL.STATE device show "$WIFI_DEV" 2>/dev/null | cut -d: -f2)"
  # Active AP profile means we're hosting the hotspot, not connected as a client.
  if nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null \
       | grep -q "^${HOTSPOT_CON}:"; then
    return 1
  fi
  # GENERAL.STATE "100 (connected)" with a non-hotspot profile = client link.
  case "$state" in
    *100*connected*) return 0 ;;
    *) return 1 ;;
  esac
}

hotspot_active() {
  nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null \
    | grep -q "^${HOTSPOT_CON}:"
}

start_hotspot() {
  log "No known WiFi — starting setup hotspot SSID=${HOTSPOT_SSID} on ${WIFI_DEV}."
  # Remove any stale profile, then create a fresh AP. NetworkManager runs its own
  # DHCP (via dnsmasq) and assigns the gateway 10.42.0.1 in 'shared' mode.
  nmcli connection delete "$HOTSPOT_CON" >/dev/null 2>&1 || true
  nmcli connection add type wifi ifname "$WIFI_DEV" con-name "$HOTSPOT_CON" \
    autoconnect no ssid "$HOTSPOT_SSID" >/dev/null
  nmcli connection modify "$HOTSPOT_CON" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASSWORD" >/dev/null
  if nmcli connection up "$HOTSPOT_CON" >/dev/null 2>&1; then
    log "Hotspot up. Connect a phone to '${HOTSPOT_SSID}' and open http://10.42.0.1:8000/wifi"
    return 0
  fi
  log "Failed to bring up hotspot."
  return 1
}

stop_hotspot() {
  if hotspot_active; then
    log "Tearing down hotspot."
    nmcli connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true
  fi
}

# --- main ---------------------------------------------------------------
# 1. Give NetworkManager a chance to auto-join a saved network after boot.
log "Waiting up to ${WAIT_SECS}s for a known WiFi on ${WIFI_DEV}..."
waited=0
while [ "$waited" -lt "$WAIT_SECS" ]; do
  if wifi_client_connected; then
    log "Connected to a known WiFi — no hotspot needed."
    exit 0
  fi
  sleep 5
  waited=$((waited + 5))
done

# 2. Still no WiFi: start the hotspot and keep trying to reconnect to a client.
start_hotspot || exit 1

while true; do
  sleep "$RECHECK_SECS"
  # Ask NetworkManager to re-scan and try auto-connecting any saved profile.
  # Briefly drop the hotspot so the single radio can scan/associate as a client.
  stop_hotspot
  nmcli device wifi rescan ifname "$WIFI_DEV" >/dev/null 2>&1 || true
  sleep 8
  if wifi_client_connected; then
    log "A known WiFi reconnected — leaving hotspot off."
    exit 0
  fi
  # Nothing to join; bring the hotspot back up and keep waiting.
  hotspot_active || start_hotspot || exit 1
done
