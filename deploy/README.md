# Raspberry Pi deployment

Run Job Hunter on a Raspberry Pi so it starts automatically on boot, updates
itself (OS + app + Ollama) on every boot, and needs no manual setup afterwards.

## One-time setup

On the Pi (Raspberry Pi OS Bookworm or newer):

```bash
git clone https://github.com/ounis/job.git ~/job
cd ~/job
sudo ./deploy/setup.sh
```

This installs OS dependencies, Ollama (+ the `llama3.1` model), the Python venv
and app dependencies, seeds `.env` (defaulting `AI_PROVIDER=ollama`), and
installs two systemd services. When it finishes it prints the LAN URL, e.g.
`http://192.168.0.42:8000`.

Options (environment variables):

```bash
sudo OLLAMA_MODEL=llama3.1 APP_PORT=8000 OS_UPDATE=security ./deploy/setup.sh
```

- `OLLAMA_MODEL` — model to pull/keep (default `llama3.1`).
- `APP_PORT` — web UI port (default `8000`).
- `OS_UPDATE` — how much to update the OS on each boot:
  - `none` — never touch OS packages (fastest boot).
  - `security` — security updates only (default).
  - `full` — full `apt full-upgrade` every boot (thorough, slower boot).

## What happens on every boot

1. `job-hunter-update.service` runs `deploy/update.sh` (best-effort, won't block
   boot): `git pull` latest `main` + refresh venv deps, upgrade Ollama and ensure
   the model is present, then apply OS updates per `OS_UPDATE`.
2. `ollama.service` serves the model. (The Ollama installer usually creates this;
   on some images, e.g. Debian 13 / Pi OS trixie, it does not — `setup.sh` creates
   the unit itself if missing.)
3. `job-hunter.service` starts the web app on `0.0.0.0:<port>`, reachable on the LAN.
   It is intentionally **not** ordered after the update service, so a slow or
   failed boot-time update never prevents the app from starting.
4. `wifi-fallback.service` watches for network: if no known WiFi connects within
   ~60s, it brings up a setup hotspot (see below).

All updates are best-effort: with no/poor internet (e.g. right after moving the
Pi) the app still starts with whatever is already installed.

## No API keys required

By default the Pi runs entirely offline-capable with **zero external API keys**:

- **AI**: local Ollama (`AI_PROVIDER=ollama`) — no key.
- **Jobs**: `bundesagentur` is the only keyless source (public default client id).
  Set `ACTIVE_PROVIDERS=bundesagentur` in `.env`.

Other job sources (JSearch/RapidAPI, Adzuna, Serper) require your own keys and
simply self-disable when their key is absent, so nothing breaks without them.

## Uploading a CV (headless-friendly)

On the dashboard, use **Upload CV** to send a `.pdf`/`.docx`/`.txt`/`.md` file
from your browser. It is saved to `data/`, set as the active CV, and parsed
immediately — no shell access needed. Handy on a headless Pi.

## Changing the device's WiFi after moving it

Two cases:

1. **The Pi is still reachable on a network** — open `http://<pi-ip>:8000/wifi`
   from a phone/laptop on the same network, scan, and join the new network.

2. **The Pi is somewhere with no known WiFi (just relocated)** — it can't join
   anything, so you can't reach the `/wifi` page directly. The **fallback hotspot**
   handles this: if no known WiFi connects within ~60s of boot, the Pi starts its
   own access point:

   - Connect a phone/laptop to WiFi **`JobHunter-Setup`** (password **`jobhunter`**).
   - Open **`http://10.42.0.1:8000/wifi`**.
   - Pick the new network and enter its password.
   - The Pi joins it and drops the hotspot.

   The hotspot SSID/password and trigger are configurable in
   `/etc/default/job-hunter-wifi` (`HOTSPOT_SSID`, `HOTSPOT_PASSWORD`, `WAIT_SECS`).

   > Note: the Pi has a single WiFi radio, so while the setup hotspot is up it has
   > no internet. That's expected — it only lasts until you pick a real network.

## Remote recovery (IMPORTANT — set this up before handing the device to someone)

If the Pi is given to someone on a network you don't control, you **cannot** fix
it remotely unless it can "dial home". Set up a mesh VPN / reverse tunnel while the
device is still reachable:

- **Tailscale** (recommended, free tier): `curl -fsSL https://tailscale.com/install.sh | sh`
  then `sudo tailscale up`. The Pi then has a stable address you can SSH to from
  anywhere, regardless of the remote network/NAT.
- Alternatives: ZeroTier, or a reverse-SSH tunnel to a VPS you own.

Without one of these, the only recovery options for a relocated device are
physical: the fallback hotspot above, an Ethernet cable, or a monitor + keyboard
(login `rpi` / `rpi`).

## Runtime config

Boot-time updater settings live in `/etc/default/job-hunter` (written by setup):

```
APP_DIR=/home/<user>/job
APP_USER=<user>
OLLAMA_MODEL=llama3.1
OS_UPDATE=security
```

WiFi fallback hotspot settings live in `/etc/default/job-hunter-wifi`:

```
HOTSPOT_SSID=JobHunter-Setup
HOTSPOT_PASSWORD=jobhunter
WIFI_DEV=wlan0
WAIT_SECS=60
```

Edit either and reboot to apply.

## Useful commands

```bash
sudo systemctl status job-hunter         # app status
journalctl -u job-hunter -f              # follow app logs
journalctl -u job-hunter-update -b       # this boot's update log
journalctl -u wifi-fallback -b           # this boot's wifi-fallback log
sudo systemctl restart job-hunter        # restart the app
```
