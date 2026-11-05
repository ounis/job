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
2. `ollama.service` (installed by the Ollama installer) serves the model.
3. `job-hunter.service` starts the web app on `0.0.0.0:<port>`, reachable on the LAN.

All updates are best-effort: with no/poor internet (e.g. right after moving the
Pi) the app still starts with whatever is already installed.

## Changing the device's WiFi after moving it

Open `http://<pi-ip>:8000/wifi` from a phone/laptop on the same network to scan
and join a new network. (If the Pi isn't on any network yet, connect it by
Ethernet once, or re-run WiFi setup via the SD/USB boot partition.)

## Runtime config

Boot-time updater settings live in `/etc/default/job-hunter` (written by setup):

```
APP_DIR=/home/<user>/job
APP_USER=<user>
OLLAMA_MODEL=llama3.1
OS_UPDATE=security
```

Edit and reboot to apply.

## Useful commands

```bash
sudo systemctl status job-hunter        # app status
journalctl -u job-hunter -f             # follow app logs
journalctl -u job-hunter-update -b      # this boot's update log
sudo systemctl restart job-hunter       # restart the app
```
