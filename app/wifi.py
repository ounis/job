"""WiFi management via NetworkManager's `nmcli`.

Lets the app reconfigure WiFi from the web UI — the point being that when the
Raspberry Pi is moved to a new place, you can join the new network from a phone/
laptop browser without a keyboard/monitor on the Pi.

Design notes:
  - Raspberry Pi OS (Bookworm and later) uses NetworkManager, so `nmcli` is the
    right tool. We shell out to it rather than add a dependency.
  - Everything degrades gracefully when nmcli is absent (e.g. on the dev Mac):
    is_available() returns False and the UI shows an "unavailable here" notice,
    so the app still runs everywhere.
  - nmcli arguments are passed as a list (never a shell string) so an SSID or
    password with spaces/special characters can't break or inject commands.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .logging_setup import get_logger

log = get_logger("app.wifi")

# WiFi radio device type in nmcli output.
_WIFI_TYPE = "wifi"
_TIMEOUT = 25  # seconds for nmcli calls (scanning/connecting can be slow)


@dataclass
class WifiNetwork:
    """A visible access point from a scan."""
    ssid: str
    signal: int           # 0-100
    security: str         # e.g. "WPA2", "" for open
    in_use: bool          # currently connected to this one


@dataclass
class WifiStatus:
    """Current WiFi state for display."""
    available: bool       # is nmcli usable on this host at all
    radio_on: bool
    connected_ssid: str   # "" when not connected
    ip_address: str       # "" when unknown
    message: str = ""     # human note when unavailable / on error


def is_available() -> bool:
    """True if nmcli exists on this host (i.e. we can manage WiFi here)."""
    return shutil.which("nmcli") is not None


def _run(args: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    """Run `nmcli <args>` safely (list form, no shell) and capture output."""
    cmd = ["nmcli", *args]
    log.info("nmcli %s", " ".join(args))
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _terse(args: list[str], fields: str, extra: list[str] | None = None,
           timeout: int = _TIMEOUT) -> list[list[str]]:
    """Run nmcli in terse, field-scoped mode and return rows split by ':'.

    Terse mode (`-t -f <fields>`) is machine-readable; NetworkManager escapes
    literal ':' inside values as '\\:', so we split on unescaped colons only.
    """
    full = ["-t", "-f", fields, *args, *(extra or [])]
    proc = _run(full, timeout=timeout)
    if proc.returncode != 0:
        log.warning("nmcli %s failed (rc=%s): %s", " ".join(args),
                    proc.returncode, (proc.stderr or "").strip()[:200])
        return []
    rows = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        rows.append(_split_terse(line))
    return rows


def _split_terse(line: str) -> list[str]:
    """Split an nmcli terse line on unescaped ':' and unescape '\\:'→':'."""
    out, buf, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            buf.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return out


def status() -> WifiStatus:
    """Return the current WiFi status for display."""
    if not is_available():
        return WifiStatus(
            available=False, radio_on=False, connected_ssid="", ip_address="",
            message="WiFi management is only available on the device "
                    "(NetworkManager/nmcli not found on this host).",
        )
    # Radio on/off.
    radio = _run(["radio", "wifi"])
    radio_on = radio.returncode == 0 and radio.stdout.strip().lower() == "enabled"

    # Active WiFi connection + its device.
    connected_ssid, device = "", ""
    for row in _terse(["-f", "ACTIVE,SSID,DEVICE", "device", "wifi"], "ACTIVE,SSID,DEVICE"):
        if len(row) >= 3 and row[0] == "yes":
            connected_ssid, device = row[1], row[2]
            break

    # IP address of the WiFi device (first IPv4).
    ip_address = ""
    if device:
        for row in _terse(["device", "show", device], "IP4.ADDRESS"):
            if row and row[0]:
                ip_address = row[0].split("/")[0]
                break

    return WifiStatus(
        available=True, radio_on=radio_on,
        connected_ssid=connected_ssid, ip_address=ip_address,
    )


def scan() -> list[WifiNetwork]:
    """Scan for visible WiFi networks, strongest signal first, de-duplicated."""
    if not is_available():
        return []
    # `--rescan yes` forces a fresh scan rather than cached results.
    rows = _terse(
        ["device", "wifi", "list", "--rescan", "yes"],
        "IN-USE,SSID,SIGNAL,SECURITY",
    )
    seen: dict[str, WifiNetwork] = {}
    for row in rows:
        if len(row) < 4:
            continue
        in_use_raw, ssid, signal_raw, security = row[0], row[1], row[2], row[3]
        if not ssid:  # hidden network with no SSID — skip
            continue
        try:
            signal = int(signal_raw)
        except ValueError:
            signal = 0
        net = WifiNetwork(
            ssid=ssid, signal=signal,
            security=security or "",
            in_use=in_use_raw.strip() == "*",
        )
        # Keep the strongest entry per SSID (APs repeat across bands/channels).
        if ssid not in seen or signal > seen[ssid].signal:
            seen[ssid] = net
    return sorted(seen.values(), key=lambda n: (not n.in_use, -n.signal))


def connect(ssid: str, password: str = "") -> tuple[bool, str]:
    """Connect to an SSID. Returns (ok, message).

    Uses `nmcli device wifi connect`, which creates/updates the connection
    profile and persists it, so the Pi reconnects automatically after a reboot.
    An empty password attempts an open-network connection.
    """
    if not is_available():
        return False, "WiFi management is not available on this host."
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "Please choose or enter a network name (SSID)."

    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    try:
        proc = _run(args, timeout=45)
    except subprocess.TimeoutExpired:
        return False, f"Timed out trying to connect to {ssid!r}."

    if proc.returncode == 0:
        log.info("Connected to WiFi SSID %r", ssid)
        return True, f"Connected to {ssid}."
    err = (proc.stderr or proc.stdout or "").strip()
    log.warning("WiFi connect to %r failed: %s", ssid, err[:200])
    # Common failure: wrong password shows up as a secrets/activation error.
    if "secrets were required" in err.lower() or "802-11-wireless-security" in err.lower():
        err = "Connection failed — the password may be incorrect."
    return False, f"Could not connect to {ssid}: {err[:200]}"
