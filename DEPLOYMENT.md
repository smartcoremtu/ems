# Deployment & Testing Guide

How the SmartCORE HEMS is built, tested and rolled out with balenaCloud, and how a new box is
provisioned. Written from the procedure actually used on 22 September 2026, including the things
that went wrong.

Fleet: **`smartcoremtu/smartcore`** · device type `raspberrypi5` (one Pi 4 in the lab) · balena CLI
on the developer laptop, logged in as the shared `smartcoremtu` account.

---

## 1. Prerequisites

- **Hardware:** Raspberry Pi 5 (4 GB is enough), SD card, Ethernet or Wi‑Fi. Optional: a Zigbee
  USB coordinator (Sonoff ZBDongle‑E) if the site uses the frient meter interface; three LEDs on
  GPIO 17 / 27 / 22 if you want the status lights.
- **Software:** [balena CLI](https://docs.balena.io/reference/balena-cli/). Version 22.x still
  works but prints a deprecation warning; upgrade when convenient.
- **Access:** the shared balenaCloud account, and an SSH key registered with
  `balena ssh-key add` if you want `balena device ssh`.

No Docker is needed locally: `balena push` builds in the cloud.

---

## 2. Fleet variables

Set them **once per fleet, scoped to a service** (Dashboard → Fleet → Variables → *Service
variables*, or the CLI below). A fleet-wide variable restarts every service on every device when
it changes; a service variable restarts only that service.

```bash
F=smartcoremtu/smartcore
balena env set INFLUX_TOKEN      "<token>"        --fleet $F --service homeassistant
balena env set CONFIG_USER       admin            --fleet $F --service nginx-reverse-proxy
balena env set CONFIG_PASSWORD   "<password>"     --fleet $F --service nginx-reverse-proxy
balena env set PORTAL_PASSPHRASE "<8+ chars>"     --fleet $F --service wifi-connect
balena env list --fleet $F
```

| Variable | Service | Required | Notes |
|---|---|---|---|
| `INFLUX_TOKEN` | homeassistant | yes | Read by `configuration.yaml` (`!env_var INFLUX_TOKEN`). Must match the token created in InfluxDB on first setup. |
| `CONFIG_USER` / `CONFIG_PASSWORD` | nginx-reverse-proxy | yes | nginx builds an htpasswd file from them at start. If unset, `/configurator/` returns 403/500 (fails closed). |
| `PORTAL_PASSPHRASE` | wifi-connect | yes | WPA2 passphrase for the "WiFi Connect" hotspot. Unset = open hotspot. Use a different string from `CONFIG_PASSWORD`: anyone in radio range can attempt this one. |
| `PORTAL_LISTENING_PORT` | wifi-connect | set | The fleet uses `8080`; the portal is then at `http://192.168.42.1:8080`. |
| `PORTAL_MAX_MINUTES` | system-manager | optional | Default `10`. `0` disables the auto-stop. |

---

## 3. Build → test on one device → roll out

Never `balena push <fleet>` without `--draft` unless you mean to update every device at once.

### 3.1 Build a draft release

```bash
cd ems
git status --short          # on the branch you want to ship; nothing unexpected
balena push smartcoremtu/smartcore --draft
```

The whole directory is uploaded to the builder, filtered by **`.dockerignore`** (not
`.gitignore`). `data/`, `analysis/` and `docs/` are excluded there; check that file before pushing
if you add large or sensitive folders.

The build prints the release commit at the end. Draft releases show `IS FINAL: false` in
`balena release list smartcoremtu/smartcore` and are **not** deployed to anyone.

### 3.2 Pin one test device to the draft

Prefer a lab box (MTU Nash Office `858445d`, MTULab `17e3efa`) over a household. Use the
**full** 32‑character commit: the short form is not resolved for draft releases.

```bash
balena device pin 858445d <full-commit>
balena device 858445d --json | grep -E 'should_be_running|is_running'
```

Download takes 5–15 minutes (deltas). `balena device <uuid>` shows `COMMIT: N/a` on a device
that has never settled a release, so use the `--json` fields or the dashboard.

### 3.3 Verify

Log-based checks (`balena device ssh <uuid>` gives a root shell on the host OS):

```bash
balena-engine ps --format '{{.Names}}\t{{.Status}}'        # 8 containers Up
balena-engine logs --tail 20 $(balena-engine ps --format '{{.Names}}' | grep ^system-manager)
#   want: "Internet up" every 30 s; no traceback; no "permission denied"
balena-engine logs $(balena-engine ps -a --format '{{.Names}}' | grep ^wifi-connect)
#   box with a network:  "Network is up - skipping WiFi Connect"
#   box with no network: "No network - starting WiFi Connect" ... "Access point 'WiFi Connect' created"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/configurator/                  # 401
curl -s -o /dev/null -w '%{http_code}\n' -u "$USER:$PASS" http://127.0.0.1/configurator/ # 200
curl -s -o /dev/null -w '%{http_code}\n' -m 3 http://127.0.0.1:3218/                     # refused
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8123/                          # 200
curl -s http://127.0.0.1:8086/health                                                     # "pass"
```

Physical checks: no "WiFi Connect" SSID visible on a phone while the box has a network. To test
the portal, give the box no network (disable Wi‑Fi autoconnect and unplug Ethernet:
`nmcli connection modify "<ssid>" connection.autoconnect no; reboot`). Within ~2 minutes the
hotspot appears **with a padlock**; the portal at `http://192.168.42.1:8080` lists networks; after
you pick one the hotspot disappears and the box comes online. Left alone, the watchdog stops the
hotspot after `PORTAL_MAX_MINUTES`.

### 3.4 Roll out

```bash
balena release finalize <full-commit>          # becomes the fleet's target
balena device track-fleet 858445d              # test box follows the fleet again
```

Online devices update within minutes; offline ones when they next connect. Watch
`balena device list --fleet smartcoremtu/smartcore` and check the household boxes as in 3.3.

### 3.5 Rollback

```bash
balena device pin <uuid> <previous-full-commit>   # one device
# or, for the fleet: finalize / pin to the previous release in the dashboard
```

---

## 4. Provisioning a new box

1. Dashboard → fleet → **Add device**. Choose the **Production** balenaOS image for anything that
   leaves the lab (a *Development* image allows passwordless root SSH on port 22222 to anyone on
   the same network). Configure Ethernet or the site's Wi‑Fi in the image if known; otherwise the
   setup hotspot handles it on site.
2. Flash with balenaEtcher, boot, wait for the device to appear online. It downloads the fleet's
   current release automatically.
3. **First-run setup, on the device** (these live in volumes, not in git):
   - InfluxDB: open `http://<ip>/influx/`, create org `hems`, bucket `home_assistant`, and a
     token; put the token in the `INFLUX_TOKEN` variable for that device if it differs from the
     fleet value (`balena env set INFLUX_TOKEN ... --device <uuid> --service homeassistant`).
   - Home Assistant: open `http://<ip>/`, create the owner account, then add the site's
     integration: **ZHA** (plug in the dongle first), **Solarman** or **Sigenergy** via HACS.
   - Optionally the ESB smart-meter interface: pair the frient EMIZB‑141 through ZHA.
4. Rename the device in the dashboard to the site's name.

Note: the repo's `services/homeassistant/config/` is copied into the image, but the `hass-config`
volume shadows it on every device that has already booted once. Changes to `configuration.yaml`
in git therefore **do not reach existing devices**; edit them through `/configurator/` or on the
device.

---

## 5. Things that have bitten us

| Symptom | Cause | Fix |
|---|---|---|
| Device shows `COMMIT: N/a`, supervisor "unhealthy", "applying changes" forever | `system-manager` never started (`exec /tmp/start.sh: permission denied` — scripts lost their executable bit on Windows checkouts). The supervisor retries every 15 min and **cannot apply any other update while the step fails**. | Fixed in the Dockerfile (`chmod +x`). Rebuild. |
| Open, unencrypted "WiFi Connect" hotspot from a box that is on Ethernet | Old `start.sh` only checked for a Wi‑Fi association. On Ethernet it started the portal and nothing ever stopped it (the watchdog was down). | Fixed: portal only when there is no default route and no Wi‑Fi; watchdog stops it after 10 min; `PORTAL_PASSPHRASE` set. |
| Pinning a release does nothing; supervisor log shows `releaseId: 1`, `appId: 1` | Device is in **local mode** (`RESIN_SUPERVISOR_LOCAL_MODE=1`, from a `balena push <ip>` during development). Cloud target is ignored. | `balena device local-mode <uuid> --disable`. This deletes the local-mode containers and volumes. |
| `balena device pin` says "Release not found" | Short commit hash on a draft release. | Use the full commit hash. |
| wifi-connect image fails to build: `apt-get ... returned a non-zero code: 100` | Base image was Debian bullseye, end of life 2026‑08‑31, removed from `deb.debian.org`. | Now `debian:bookworm`. |
| `/configurator/` returns 403/500 | `CONFIG_USER` / `CONFIG_PASSWORD` not set. | Set the service variables. |
| Portal not visible on the phone | Portal only starts at boot and only with no network; the watchdog stops it after 10 min. Some phones hide 2.4 GHz networks briefly. | Reboot the box with no network; refresh the Wi‑Fi list. |
| Piped `balena device ssh` commands fail with `$'\357\273\277...': command not found` | PowerShell added a UTF‑8 BOM. | Pipe from a file written without BOM, or type interactively. |
| Household data staged in git | `/data/` is only ignored on some branches. | Check `.gitignore` has `/data/` before `git add`. Never `git add -A` blindly. |

---

## 6. Useful commands

```bash
balena device list --fleet smartcoremtu/smartcore
balena device <uuid> --json                 # target/running release, IP, OS
balena device ssh <uuid>                    # host OS shell
balena device ssh <uuid> homeassistant      # shell inside a service
balena logs <uuid> --service system-manager --tail
balena device tunnel <uuid> -p 80:8080      # http://127.0.0.1:8080 → nginx on the box
balena env list --fleet smartcoremtu/smartcore
balena release list smartcoremtu/smartcore
```

Health checks from inside any bridge-network container:

```bash
curl -s http://172.18.4.2:8123 | head -5    # Home Assistant
curl -s http://172.18.4.3:8086/health       # InfluxDB
```

---

## 7. Known gaps (not yet addressed)

- All fleet devices run **development** balenaOS images (passwordless root SSH on :22222 from
  the LAN). Move to production images at the next reflash.
- Mosquitto is `allow_anonymous true` on a published port; harmless while idle, harden before
  any MQTT device is added.
- `system-manager` logs at DEBUG and prints the supervisor API key in request URLs.
- The nginx `stream` block proxies `:7845` to an `eesmart-d2l` container that does not exist.
- `led-status`'s `time.sleep(2)` sits outside its loop, so it polls continuously.
