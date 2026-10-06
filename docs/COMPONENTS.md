# SmartCORE HEMS — Component Reference

**Project:** SmartCORE Ireland — Home Energy Management System
**Organisation:** Munster Technological University (MTU)
**Platform:** Raspberry Pi 5 · balenaOS · docker-compose (8 services) · balenaCloud fleet `smartcoremtu/smartcore`

A technical reference for every component on the box: what it is, what it does, how it connects
to the rest, and — because a lot of the system is configured on the device rather than in git —
what you will find on a deployed unit that is *not* in this repository. Where this document and
the code disagree, trust the code; where the code and a device disagree, the device audit notes
say so.

Companion documents: [`../DEPLOYMENT.md`](../DEPLOYMENT.md) (build, test, roll out),
[`MQTT_AND_MOBILE_ACCESS.md`](MQTT_AND_MOBILE_ACCESS.md) (MQTT concepts, the Zigbee2MQTT
alternative, and remote-access options — none of which is deployed), and the diagram
[`architecture.png`](architecture.png) / [`architecture_diagram.puml`](architecture_diagram.puml).

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Physical Layer — What the Box Measures](#2-physical-layer--what-the-box-measures)
3. [Docker Infrastructure — The HEMS Bridge Network](#3-docker-infrastructure--the-hems-bridge-network)
4. [Bridge Network Services](#4-bridge-network-services)
   - [4.1 Home Assistant](#41-home-assistant--17218428123)
   - [4.2 InfluxDB](#42-influxdb--17218438086)
   - [4.3 MQTT Broker (Mosquitto)](#43-mqtt-broker-mosquitto--17218471883)
   - [4.4 Nginx Reverse Proxy](#44-nginx-reverse-proxy--172184480)
   - [4.5 hass-configurator](#45-hass-configurator--17218463218)
   - [4.6 led-status](#46-led-status--1721849)
5. [Host Network Services](#5-host-network-services)
   - [5.1 system-manager](#51-system-manager)
   - [5.2 wifi-connect](#52-wifi-connect)
6. [balenaCloud](#6-balenacloud)
7. [What Lives on the Device but Not in Git](#7-what-lives-on-the-device-but-not-in-git)
8. [Known Issues](#8-known-issues)

---

## 1. System Overview

The HEMS is a containerised energy-monitoring gateway. It:

1. **Collects** energy measurements from whatever the site has — a solar inverter, a battery
   system, or the ESB smart meter's pulse LED — through Home Assistant integrations
2. **Stores** them as time series in a local InfluxDB (nothing leaves the house unless an
   integration is configured to send it)
3. **Presents** them through one web entry point (nginx on port 80) and the Home Assistant app
4. **Self-heals**: restarts Home Assistant after an update, reboots after 30 minutes without
   internet, and is updated over the air by balenaCloud
5. **Indicates** health with three LEDs on the Pi's GPIO header

Eight containers run under `docker-compose.yml` on a Raspberry Pi 5 with balenaOS. balenaCloud is
the deployment and remote-management plane.

---

## 2. Physical Layer — What the Box Measures

The box is **equipment-agnostic**: the sensing hardware differs per site and is set up through the
Home Assistant UI, not in this repository. Three arrangements are deployed (September 2026):

### 2.1 Solar inverter via a Solarman logger

A Sofar G3 inverter with a Solarman (LSW) Wi‑Fi data logger on the home LAN. Home Assistant polls
it over Modbus/TCP with the **`solarman`** custom component (installed through HACS). It exposes
~36 entities: grid / load / inverter / PV power, string voltages and currents, grid voltage and
frequency, daily and lifetime energy counters, inverter temperatures, state and fault text. This
is the richest data source in the fleet and the one the energy report on the analysis branch was
built from. Battery power and state of charge are *not* exposed by this integration on the
current firmware; the analysis estimates them.

### 2.2 Sigenergy inverter and battery

A Sigenergy hybrid inverter with battery, read over Modbus/TCP by the **`sigenergy`** custom
component. Provides PV, battery, grid and load figures directly from the system.

### 2.3 ESB smart meter pulse LED via Zigbee

The utility smart meter has no data interface for the homeowner, but its front-panel LED blinks
once per Wh (1000 pulses = 1 kWh). A **frient Electricity Meter Interface, model EMIZB‑141**, is
stuck over that LED, counts the pulses, and reports instantaneous demand and cumulative
consumption over Zigbee (Smart Energy cluster 0x0702). A USB Zigbee coordinator on the Pi —
**Sonoff ZBDongle‑E** (Silicon Labs EFR32MG21, EZSP serial protocol) — receives the frames, and
the **ZHA** integration inside Home Assistant decodes them into `sensor.*` entities.

```
Smart meter LED → frient EMIZB-141 → IEEE 802.15.4 → ZBDongle-E (/dev/ttyUSB0) → ZHA in Home Assistant
```

The Home Assistant container runs `privileged: true` so the USB serial device is visible inside
it. Import only: this arrangement cannot see export, solar or battery, and the ESB meter's own
half-hourly data (downloadable from the ESB Networks portal) remains the billing reference.

> Earlier versions of this document and the June 2026 partner presentation call the Zigbee device a
> "Frient SMSZB‑120". That is frient's smoke alarm; the meter interface is the EMIZB‑141.

---

## 3. Docker Infrastructure — The HEMS Bridge Network

Docker creates a virtual switch inside the Pi's kernel. Containers that join it get fixed IPs and
can talk to each other; nothing outside the Pi can reach a container unless a port is published.

```yaml
networks:
  hems:
    driver: bridge
    ipam:
      config:
        - subnet: 172.18.4.0/24
          gateway: 172.18.4.1
```

| Address | Service |
|---|---|
| `172.18.4.1` | gateway (the bridge itself) |
| `172.18.4.2` | homeassistant |
| `172.18.4.3` | influxdb |
| `172.18.4.4` | nginx-reverse-proxy |
| `172.18.4.6` | hass-configurator |
| `172.18.4.7` | mqtt |
| `172.18.4.9` | led-status |

The addresses are static because they are hard-coded in three places: `configuration.yaml`
(`influxdb: host: 172.18.4.3`), `led.py` (`HA_IP = "172.18.4.2"`) and nginx `http.conf`
(`proxy_pass` targets).

**Published on the Pi's real network interface:**

```
:80    → nginx-reverse-proxy   primary entry point (HA, /influx/, /configurator/)
:8123  → homeassistant         direct HA access (bypasses nginx)
:6053  → homeassistant         ESPHome native API port (published, unused)
:8086  → influxdb              direct InfluxDB API/UI
:1883  → mqtt                  MQTT broker (idle)
:7845  → nginx stream proxy    to a container that does not exist (see Known Issues)

not published: hass-configurator (:3218), led-status
```

`system-manager` and `wifi-connect` use `network_mode: host` and have no bridge address.

---

## 4. Bridge Network Services

### 4.1 Home Assistant — `172.18.4.2:8123`

**Image:** `ghcr.io/home-assistant/home-assistant:2026.6.3` (`services/homeassistant/docker/Dockerfile`)
**Volume:** `hass-config` → `/config`
**Role:** owns the site's equipment through its integrations, holds every entity's state, writes
every `sensor` change to InfluxDB, serves the UI.

The image build copies `services/homeassistant/config/` to `/config`, but on any device that has
booted once the `hass-config` **volume shadows that directory**. Repo changes to
`configuration.yaml` therefore never reach an existing device; the copy in git is the *initial*
config for a fresh box. The device audit of September 2026 found the on-device
`configuration.yaml`, `automations.yaml` and `purge_backups.sh` still byte-identical to git, so
the two have not drifted yet.

#### Integrations that produce data

Configured through the HA UI, stored in `/config/.storage/core.config_entries` inside the volume:

| Integration | Kind | Where |
|---|---|---|
| `solarman` | HACS custom component, Modbus/TCP to a Solarman logger | inverter site |
| `sigenergy` | custom component, Modbus/TCP | Sigenergy site |
| `zha` | built-in; owns the USB coordinator; frient EMIZB‑141 paired | two meter-LED sites |
| `energyid` | custom; pushes readings to `hooks.energyid.eu` (the only integration that sends data off-site — check it against the ethics application) | inverter site |
| `hacs` | custom-component store, installed on the device | most sites |

There is **no MQTT integration configured on any site** (see 4.3). ZHA and Solarman do not use
MQTT.

#### The data path inside Home Assistant

Every integration updates entities in the **state machine**; each change fires a `state_changed`
event on the internal **event bus**; the **InfluxDB integration** listens and writes:

```yaml
influxdb:
  api_version: 2
  ssl: false
  host: 172.18.4.3
  port: 8086
  token: !env_var INFLUX_TOKEN
  organization: hems
  bucket: home_assistant
  include:
    domains:
      - sensor
```

Only the `sensor` domain is written. HA only writes when a value **changes**, so a flat signal
produces no points — a long run without points is normally "unchanged", not "no data". Points
are posted immediately as line protocol:

```
POST http://172.18.4.3:8086/api/v2/write?org=hems&bucket=home_assistant
Authorization: Token <INFLUX_TOKEN>

W,domain=sensor,entity_id=inverter_grid_power,friendly_name=... value=1020 1790089562559901000
```

Note the **measurement is the unit** (`W`, `kWh`, `V`, `Hz`, `°C`), which is Home Assistant's
default for the InfluxDB integration; the entity is the `entity_id` tag. Text states (inverter
state, fault text) are stored under a `state` field instead of `value`.

#### Other configuration in git

- `http: use_x_forwarded_for` with `trusted_proxies: 172.0.0.0/8, 127.0.0.1` so nginx can front HA.
- `automations.yaml`: a backup on the 1st of each month at 01:12, and `purge_backups.sh` nightly
  at 02:12, which deletes backups older than 90 days. Verified on a device: three monthly backups present.
- `configuration.yaml` also includes `template_sensors/`, `automations/` and `themes/`
  directories that exist **neither in git nor on the devices**. Home Assistant starts regardless.
  They are placeholders for per-site template sensors that were never used.

---

### 4.2 InfluxDB — `172.18.4.3:8086`

**Image:** `influxdb:2.7.1` · **Volume:** `influxdb-data` → `/var/lib/influxdb2`

Organisation `hems`, bucket `home_assistant`, created by hand on first setup through the UI
(`http://<ip>/influx/`); there are no init scripts. The token created there is what
`INFLUX_TOKEN` must contain.

Data layout, as written by Home Assistant:

```
bucket home_assistant
  measurement = unit  (W, kWh, V, A, Hz, °C, %, ...)
    tags   entity_id, domain, friendly_name, ...
    fields value (numeric)   or   state (text)
```

Flux example — last hour of grid power at an inverter site:

```flux
from(bucket: "home_assistant")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "W" and r.entity_id == "inverter_grid_power" and r._field == "value")
```

Volume: about 21 million points over five months at one inverter site (~5 s cadence on the power
signals). Exporting: run `influx query --raw --file` inside the container, write to `/mnt/data`,
then `balena device tunnel` + `scp` — see the project context notes. ~30 s per million points on
a Pi 5.

---

### 4.3 MQTT Broker (Mosquitto) — `172.18.4.7:1883`

**Image:** `eclipse-mosquitto` · **Volume:** `mosquitto` → `/mosquitto/data`

```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
```

**Status: idle on every deployed site.** No site has an MQTT device and Home Assistant has no
MQTT integration configured, so nothing publishes or subscribes. The broker is kept for future
devices (Wi‑Fi sensors, relays) or for the Zigbee2MQTT alternative architecture described in
[`MQTT_AND_MOBILE_ACCESS.md`](MQTT_AND_MOBILE_ACCESS.md).

`allow_anonymous true` on a port that is published to the LAN (`1883:1883`) is a known gap.
Harmless while idle; add a password file before any MQTT device is introduced.

---

### 4.4 Nginx Reverse Proxy — `172.18.4.4:80`

**Image:** `arm64v8/nginx:latest` + `apache2-utils` · **Published:** `80:80`

Single entry point so that users need one URL, and the only way to reach the configurator.

**`start.sh`** — builds the basic-auth file from the service variables, then starts nginx:

```sh
if [ -n "$CONFIG_USER" ] && [ -n "$CONFIG_PASSWORD" ]; then
    htpasswd -cb /etc/nginx/.passwd "$CONFIG_USER" "$CONFIG_PASSWORD"
else
    echo "WARNING: CONFIG_USER / CONFIG_PASSWORD are not set - /configurator/ will be refused until they are."
    rm -f /etc/nginx/.passwd
fi
nginx
sleep infinity
```

**`http.conf`** routes:

| Path | Upstream | Notes |
|---|---|---|
| `/` | `172.18.4.2:8123` Home Assistant | WebSocket upgrade headers, `X-Forwarded-For`. `set $ha` variable so nginx starts even if HA is still booting. |
| `/configurator/` | `172.18.4.6:3218` | **`auth_basic`** against `/etc/nginx/.passwd`; fails closed when the file is absent. `proxy_hide_header Authorization` so the login header does not reach the configurator. |
| `/influx/` | `172.18.4.3:8086` | Strips the prefix and rewrites the InfluxDB single-page app's absolute paths with `sub_filter`, plus an injected `/influx/env.js` that sets the app's base path. Fragile but works on 2.7.1. |

**`nginx.conf`** also has a `stream {}` block that proxies TCP `:7845` to `eesmart-d2l:7845`.
No such container is defined, so this route is dead (see Known Issues).

---

### 4.5 hass-configurator — `172.18.4.6:3218`

**Image:** `causticlab/hass-configurator-docker:latest` · **Volume:** `hass-config` → `/hass-config`

A browser-based file editor for the Home Assistant config volume. HA add-ons (file editor, SSH)
only exist on Home Assistant OS, not on a plain container install, so this is the way to edit
`configuration.yaml`, automations and scripts on a deployed box without SSH or a rebuild. It
mounts the **same volume** as HA, so a saved file is visible to HA at once; reload the relevant
integration from Developer Tools.

The configurator has no login of its own (`HC_USERNAME`/`HC_PASSWORD` are unset). It is therefore
**not published on the host** any more; the only route is `http://<ip>/configurator/` through
nginx, which demands `CONFIG_USER`/`CONFIG_PASSWORD`.

---

### 4.6 led-status — `172.18.4.9`

**Image:** Ubuntu + `gpiozero` + `lgpio` · **Device:** `/dev/gpiochip0` · **Volume:** `hass-config` (to read the HA log)

Three LEDs, driven from `led.py`:

| GPIO | Meaning | Check |
|---|---|---|
| 17 | Home Assistant reachable | `ping -c1 -W1 172.18.4.2` |
| 27 | Internet reachable | `ping -c1 -W1 8.8.8.8` |
| 22 | Error | any new line in `/hass-config/home-assistant.log` containing `ERROR` since the last read |

It sits on the bridge network (not host) because it pings HA's bridge address. `gpiozero`'s lgpio
factory is monkey-patched to open GPIO chip 0, which is what the Pi 5 needs.

Bug: `time.sleep(2)` is placed **after** the `while True:` loop, not inside it, so the loop runs
back-to-back (each iteration is throttled only by the two 1‑second ping timeouts). Harmless but
wasteful; the intended cadence is every 2 seconds.

---

## 5. Host Network Services

`network_mode: host` gives a container the Pi's real network stack — `eth0`, `wlan0`, the routing
table, iptables and `127.0.0.1` (where the balena Supervisor listens). The trade-off is that it
cannot use bridge addresses.

### 5.1 system-manager

**Image:** `arm64v8/alpine:3` + Python 3 + `requests` + iptables · **Volume:** `system-manager` → `/data`
**Restart:** `on-failure` · **Label:** `io.balena.features.supervisor-api`

`start.sh` runs `iptables.sh` in the background and then `watchdog.py` in the foreground. The
Dockerfile `chmod +x`s all of them: git on Windows does not keep the executable bit, and until
September 2026 every release built from a Windows checkout failed here with
`exec /tmp/start.sh: permission denied` — which also left the balena Supervisor unable to apply
any further update (it retries the failing start every 15 minutes).

#### iptables.sh

Waits 180 s for NetworkManager to create its chains, then inserts (once, idempotently, for both
`iptables` and `iptables-legacy`):

```
iptables -I nm-sh-fw-wlan0 -o wlan0 -s 172.18.4.0/24 -d 10.42.0.0/24 -j ACCEPT
```

This allows the Docker bridge to talk to clients of a NetworkManager shared Wi‑Fi hotspot
(`10.42.0.0/24`). No current site runs such a hotspot, so the rule is inert; the chain may not
exist on an Ethernet-only box, in which case the command fails quietly.

#### watchdog.py — every 30 seconds

Talks to the **balena Supervisor** REST API on `127.0.0.1:48484` with the injected
`BALENA_SUPERVISOR_ADDRESS` / `BALENA_SUPERVISOR_API_KEY` / `BALENA_APP_ID` / `BALENA_APP_NAME`.

1. **Setup-hotspot timer** (`check_wifi_portal_n_stop`): if the `wifi-connect` service (name from
   `WIFI_PORTAL_SERVICE`, default `wifi-connect`) has been running for more than
   `PORTAL_MAX_MINUTES` (default 10; `0` disables), stop it via `/v2/applications/<id>/stop-service`.
   The hotspot is a boot-time convenience, not something to leave broadcasting.
2. **Restart HA after an update** (`restart_hass`): compares the current `releaseId` from
   `/v2/applications/state` with `/data/version.txt`; if it changed and stayed changed for two
   minutes, restarts `homeassistant` and records the new id. Observed working on 22 Sep 2026.
3. **Internet watchdog** (`check_internet`): `ping -c 1 google.com`; if it has failed for 30
   minutes, `POST /v1/reboot`. Every action is appended to `/data/restartLog.txt`.

Logging is at DEBUG, which prints the Supervisor API key inside every request URL — reduce to INFO
before the logs are shared.

### 5.2 wifi-connect

**Image:** `debian:bookworm` + `dnsmasq wireless-tools iproute2 network-manager` + balena
`wifi-connect` v4.11.84 binary · **Network:** host · **Cap:** `NET_ADMIN` · **Label:** `io.balena.features.dbus`

`scripts/start.sh`:

1. Wait up to 60 s for a network, where "network" means a **default route** (Ethernet or Wi‑Fi)
   *or* a Wi‑Fi association (`iwgetid -r`).
2. If there is one: print `Network is up - skipping WiFi Connect`, and delete any leftover
   NetworkManager profile named `WiFi Connect` in AP mode (`nmcli`, over the host D‑Bus) — the
   hotspot outlives the container if wifi-connect was killed rather than stopped.
3. If there is none: warn if `PORTAL_PASSPHRASE` is unset, then run `wifi-connect` in the
   background (so SIGTERM reaches it). It brings up the access point **"WiFi Connect"**
   (WPA2 with `PORTAL_PASSPHRASE`; `PORTAL_SSID` would rename it), serves the captive portal on
   `192.168.42.1:<PORTAL_LISTENING_PORT>` (fleet: 8080), and when the user submits a network it
   creates the Wi‑Fi profile, tears the hotspot down and exits.
4. Then sleep forever so the container stays up (the watchdog stops the service after
   `PORTAL_MAX_MINUTES` anyway).

Before September 2026 the script only tested `iwgetid`, so every Ethernet-connected box started
an **open** hotspot on boot and never stopped it. That is what the whole system-manager /
wifi-connect fix addressed.

---

## 6. balenaCloud

Fleet **`smartcoremtu/smartcore`**, `balena.yml` name `SmartCORE`, default device type
`raspberrypi5`. Seven devices (September 2026): five households/SMEs in Co. Kerry and two MTU lab
boxes (one Pi 4). All still on **development** OS images.

What the cloud does for us:

- **Releases**: `balena push <fleet> --draft` builds all eight images in the cloud; a device can be
  pinned to a draft for testing; `balena release finalize` makes it the fleet target and every
  online device updates (deltas). See `DEPLOYMENT.md`.
- **Variables**: injected into containers at start. Service-scoped:
  `INFLUX_TOKEN` (homeassistant), `CONFIG_USER`/`CONFIG_PASSWORD` (nginx), `PORTAL_PASSPHRASE`
  (wifi-connect), `PORTAL_MAX_MINUTES` (system-manager); fleet-wide: `PORTAL_LISTENING_PORT`.
- **Supervisor API** on each device, used by system-manager to stop/restart services and reboot.
- **Remote access** for the team: `balena device ssh`, `balena device tunnel`, logs, dashboard
  terminal. This is the only remote-access path to the boxes; there is no VPN or tunnel service
  in the compose file. Homeowners reach their dashboard only on the home LAN.

---

## 7. What Lives on the Device but Not in Git

Things a fresh checkout does not show you but a deployed box has:

| Where | What |
|---|---|
| `hass-config` volume, `.storage/` | HA owner account, integrations (`solarman`, `sigenergy`, `zha`, `energyid`, `hacs`, `mobile_app`, `hue`, …), device and entity registries, dashboards |
| `hass-config` volume, `custom_components/` | `hacs`, `solarman`, `sigenergy`/`sigen` — installed per site through HACS |
| `hass-config` volume, `backups/` | monthly HA backups (three kept) |
| `influxdb-data` volume | the org, bucket, token and all data |
| `system-manager` volume | `version.txt`, `restartLog.txt` |
| balenaCloud | the variables above, device names, release pins |
| balenaOS host | NetworkManager Wi‑Fi profiles, including any created by the setup portal |

`services/homeassistant/config/custom_components/hacs/` *is* in git (423 files) so HACS is
present from first boot; everything installed through it is not.

---

## 8. Known Issues

| Issue | Detail | Status |
|---|---|---|
| Development OS images | Passwordless root SSH on `:22222` to anyone on the LAN, on every device | Open — reflash with production images |
| Anonymous MQTT on a published port | `allow_anonymous true`, `1883:1883` | Open, low impact while idle |
| Dead stream proxy | nginx `:7845` → `eesmart-d2l`, no such container | Open — remove or define |
| Supervisor API key in logs | system-manager logs at DEBUG | Open |
| `led.py` sleep outside loop | polls continuously instead of every 2 s | Open |
| Repo config shadowed by volume | `COPY config /config` never reaches provisioned devices | By design; documented |
| Missing include directories | `template_sensors/`, `automations/`, `themes/` referenced by `configuration.yaml`, exist nowhere | Cosmetic |
| `hass-configurator:latest` unpinned | image can change under us | Open |
| `EnergyID` webhook on one site | sends readings to a third-party cloud | Check against ethics application |
| Port 6053 published on HA | ESPHome API, unused | Cosmetic |

Fixed in September 2026: system-manager not starting (exec bit); open setup hotspot on Ethernet
boxes; configurator without login and published on `:3218`; `restart: on_failure` typo;
wifi-connect base image on end-of-life Debian bullseye.

---

*Rewritten 2026-09-22 from the code on branch `fix/system-manager-start-and-open-hotspot` and
inspection of the seven fleet devices. Previous version 2026-02-24.*
