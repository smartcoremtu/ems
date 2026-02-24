# SmartCORE HEMS — Component Reference

**Project:** SmartCORE Ireland — Home Energy Management System
**Organisation:** Munster Technological University (MTU)
**Platform:** Raspberry Pi 5 · BalenaOS · Docker Compose

This document is a complete technical reference for every component in the HEMS. It covers what each component is, what it does, how it connects to everything else, and concrete examples drawn from the actual codebase. It also records corrections to the current architecture diagram.

---

## Diagram Corrections

Before the component reference, the following items in the current architecture diagram are incorrect or missing and should be updated.

| Issue | Detail |
|---|---|
| **4th physical layer box (Smart Zigbee Plug / Kettle)** | This hardware does not exist in this deployment. The only physical devices are the Smart Meter, the Frient SMSZB-120, and the Zigbee USB Dongle. Remove this box and its connections. |
| **MQTT Broker missing** | Mosquitto (`172.18.4.7:1883`) is a running service on the bridge network but is not shown as its own component. It should be added to the bridge network section. |
| **hass-configurator missing** | The hass-configurator (`172.18.4.6:3218`) is a running service on the bridge network but is not shown. It should be added to the bridge network section. |

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Physical Layer](#2-physical-layer)
   - [Grid Smart Meter](#21-grid-smart-meter)
   - [Frient SMSZB-120 (Zigbee Blink Counter)](#22-frient-smszb-120-zigbee-blink-counter)
   - [Zigbee USB Dongle](#23-zigbee-usb-dongle)
3. [Docker Infrastructure — The HEMS Bridge Network](#3-docker-infrastructure--the-hems-bridge-network)
4. [Bridge Network Services](#4-bridge-network-services)
   - [Home Assistant](#41-home-assistant--17218428123)
   - [InfluxDB](#42-influxdb--172184386)
   - [MQTT Broker (Mosquitto)](#43-mqtt-broker-mosquitto--1721847-1883)
   - [Nginx Reverse Proxy](#44-nginx-reverse-proxy--17218448-80)
   - [hass-configurator](#45-hass-configurator--172184631)
   - [led-status](#46-led-status--172184992)
5. [Host Network Services](#5-host-network-services)
   - [system-manager](#51-system-manager)
   - [wifi-connect](#52-wifi-connect)
6. [Balena Cloud](#6-balena-cloud)
7. [Remote Access — Mobile Application](#7-remote-access--mobile-application)

---

## 1. System Overview

The HEMS is a containerised energy monitoring gateway. Its job is to:

1. **Collect** energy measurements from a physical smart meter via a Zigbee radio sensor
2. **Store** those measurements as time-series data in a local database
3. **Present** that data through a single web interface accessible from a browser or mobile app
4. **Self-heal** by automatically restarting services, deploying updates, and rebooting on failure
5. **Indicate** system health visually via physical LEDs on the Pi

All logic runs in eight Docker containers orchestrated by `docker-compose.yml` on a Raspberry Pi 5 running BalenaOS. Balena Cloud provides the remote deployment and management plane.

---

## 2. Physical Layer

### 2.1 Grid Smart Meter

The grid smart meter is the utility-installed electricity meter in the home. It is a passive physical device — it has no network interface and no software. It measures the home's total electricity consumption and records it.

The only interface available for real-time monitoring is the **S0 pulse LED output**: a small red LED on the meter front panel that blinks at a fixed rate proportional to power consumption. The standard Irish/EU pulse constant is **1000 pulses per kWh**, meaning:

```
Instantaneous power (W) = 3,600,000 / (time between pulses in ms × 1000)
Accumulated energy (kWh) = total pulse count / 1000
```

The meter itself does not connect to the Pi. It connects to the Frient sensor described below.

---

### 2.2 Frient SMSZB-120 (Zigbee Blink Counter)

The Frient Electricity Meter Interface (product code SMSZB-120) is the device that reads the smart meter and brings its data into the digital system.

**What it is:** A Zigbee radio sensor with a physical S0 pulse input terminal. It clamps directly onto the smart meter's S0 output wires. When the meter LED blinks, the Frient counts the pulse.

**What it is not:** It is not a WiFi device. It does not speak MQTT. It does not connect to a broker. It is a Zigbee sensor — it communicates exclusively via the IEEE 802.15.4 radio protocol to the Zigbee USB Dongle described below.

**What it reports:** The Frient implements the Zigbee Smart Energy profile. It exposes two key Zigbee cluster attributes:
- **Cluster 0x0702, Attribute 0x0000** — Current Summation (accumulated energy in kWh)
- **Cluster 0x0702, Attribute 0x0400** — Instantaneous Demand (current power in W)

These are decoded by the ZHA integration inside Home Assistant and become `sensor.*` entities automatically.

**Connection chain:**
```
Smart Meter S0 LED → pulse wire → Frient SMSZB-120 → IEEE 802.15.4 radio → Zigbee USB Dongle
```

---

### 2.3 Zigbee USB Dongle

**Hardware:** Sonoff ZBDongle-E, based on the Silicon Labs EFR32MG21 chip.

**What it is:** A USB radio coordinator. It is the hardware bridge between the Zigbee radio network (where the Frient lives) and the Raspberry Pi. It is plugged into a USB port on the Pi.

**What it speaks:**
- **Over the air:** IEEE 802.15.4 radio frames to/from Zigbee devices
- **Over USB to the Pi:** EZSP — EmberZNet Serial Protocol — a binary serial protocol that lets software on the Pi tell the chip what to do (scan, join, transmit, receive)

The dongle has no IP address, no MQTT client, no web server. It is purely a radio controlled over a serial port. All intelligence about what to do with the Zigbee frames lives in the ZHA integration inside Home Assistant.

In `docker-compose.yml`, the Home Assistant container is given `privileged: true`, which allows Docker to pass the USB device through to the container so ZHA can open the serial port directly.

---

## 3. Docker Infrastructure — The HEMS Bridge Network

### What it is

When Docker starts, it creates a **virtual network switch** entirely inside the Linux kernel of the Raspberry Pi. No physical hardware is involved. Every container that joins this switch gets its own virtual network interface with a fixed IP address. Containers on the same switch can talk to each other by IP. The outside world — any device outside the Pi — **cannot reach a container unless a port is explicitly published** in `docker-compose.yml`.

From `docker-compose.yml`:
```yaml
networks:
  hems:
    driver: bridge
    ipam:
      config:
        - subnet: 172.18.4.0/24
          gateway: 172.18.4.1
```

### The IP address table

Every service on the bridge has a fixed, static IP:

| Address | Service |
|---|---|
| `172.18.4.1` | Gateway (the virtual switch itself) |
| `172.18.4.2` | Home Assistant |
| `172.18.4.3` | InfluxDB |
| `172.18.4.4` | Nginx reverse proxy |
| `172.18.4.6` | hass-configurator |
| `172.18.4.7` | MQTT Broker (Mosquitto) |
| `172.18.4.9` | led-status |

### Why static IPs are essential

These addresses are hardcoded in three separate places across the codebase. If Docker assigned them dynamically they would change on restart and silently break everything.

**In `configuration.yaml`** — HA writing to InfluxDB:
```yaml
influxdb:
  host: 172.18.4.3   # would break if InfluxDB got a different IP
```

**In `led.py`** — led-status pinging HA:
```python
HA_IP = "172.18.4.2"  # would ping the wrong container if HA moved
```

**In `http.conf`** — Nginx routing to HA and InfluxDB:
```nginx
set $ha http://172.18.4.2:8123;
proxy_pass http://172.18.4.3:8086;
```

### What is visible from outside the Pi

A port mapping (`ports:`) in docker-compose cuts a hole from the Pi's real network interface into a specific container. Everything without a mapping is completely invisible from outside:

```
Pi's real network (e.g. 192.168.1.50)
  :80   ─────► nginx         172.18.4.4:80    (primary UI entry point)
  :8123 ─────► homeassistant 172.18.4.2:8123  (direct HA access)
  :8086 ─────► influxdb      172.18.4.3:8086  (direct InfluxDB access)
  :1883 ─────► mqtt          172.18.4.7:1883  (external MQTT clients)
  :3218 ─────► configurator  172.18.4.6:3218  (direct configurator)

  led-status (172.18.4.9) — NO port mapping, invisible from outside
```

---

## 4. Bridge Network Services

### 4.1 Home Assistant — `172.18.4.2:8123`

**Image:** `homeassistant/home-assistant:2025.3`
**Volume:** `hass-config` mounted at `/config`
**Role:** The central hub. It owns the Zigbee hardware, creates all sensor entities, writes all data to InfluxDB, and serves the primary user interface.

Home Assistant is not a simple web server. It is a Python application that runs an ecosystem of integrations simultaneously inside one process. Four are critical in this system.

---

#### ZHA Integration (Zigbee Home Automation)

ZHA is a library that runs **inside the HA process**. It owns the USB dongle directly via the USB passthrough granted by `privileged: true` in the compose file.

When the Frient blink counter sends a Zigbee radio frame, the sequence is:

```
Frient SMSZB-120
  │  IEEE 802.15.4 radio frame
  ▼
Zigbee USB Dongle
  │  EZSP binary serial over USB
  ▼
ZHA (running inside Home Assistant Python process)
  │  decodes EZSP → parses Zigbee cluster 0x0702
  │  extracts: power = 342 W, energy = 1053.2 kWh
  ▼
HA State Machine
  │  sensor.frient_power  = 342 W
  │  sensor.frient_energy = 1053.2 kWh
```

**MQTT is not involved in this path at all.** ZHA speaks directly to the hardware.

---

#### HA State Machine

The State Machine is the central registry of the system. Every device, sensor, switch, and entity in the system has a current state stored here. State entries have:
- A unique `entity_id` (e.g. `sensor.frient_power`)
- A `state` value (e.g. `342`)
- A unit of measurement (e.g. `W`)
- A set of attributes (device class, last changed, etc.)

When ZHA updates a sensor value, it writes the new value here. When the InfluxDB integration wants to know what changed, it listens here.

---

#### HA Internal Event Bus

The Event Bus is a publish/subscribe message system running entirely inside the HA Python process. When any entity's state changes, the State Machine fires a `state_changed` event onto the event bus containing the old state, the new state, and the entity ID.

**This is the pivot point of the entire data pipeline.** Every integration — InfluxDB, MQTT, automations, dashboards — reacts to events on this bus. They do not talk to each other directly; they all talk to the bus.

```
ZHA updates sensor.frient_power = 342
  │
  ▼
Event Bus fires: state_changed {
  entity_id: "sensor.frient_power",
  new_state: { state: "342", attributes: {unit: "W"} },
  old_state: { state: "338" }
}
  │
  ├──► InfluxDB Integration receives it → writes to database
  ├──► HA Dashboard receives it → updates live UI
  └──► Any automation triggers receive it → run if conditions match
```

---

#### InfluxDB Integration

Configured entirely in `configuration.yaml`:

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

This integration subscribes to the Event Bus and listens for `state_changed` events. When one arrives, it checks whether the entity's domain is `sensor`. If it matches, it formats the state as InfluxDB **line protocol** and sends it immediately via HTTP POST:

```http
POST http://172.18.4.3:8086/api/v2/write?org=hems&bucket=home_assistant
Authorization: Token <INFLUX_TOKEN>
Content-Type: text/plain

sensor,entity_id=frient_power,unit_of_measurement=W value=342.0 1708789012000000000
```

The token is never stored in the codebase — it is injected as a Balena environment variable (`INFLUX_TOKEN`) at runtime via `!env_var INFLUX_TOKEN`.

**Important:** The `include: domains: [sensor]` filter means only entities in the `sensor` domain are written. `binary_sensor`, `switch`, `light`, `automation`, and all other domains are ignored. This keeps the database focused on measurement data.

---

#### MQTT Integration

Configured via the HA web UI (not in `configuration.yaml`). The broker address `172.18.4.7:1883` is stored at runtime in `/config/.storage/core.config_entries` inside the `hass-config` volume — this file is not committed to git.

The MQTT integration is **not in the Zigbee data path**. The Frient sensor goes entirely through ZHA. The MQTT broker is present as infrastructure for any future device that speaks native MQTT (WiFi-based sensors, relays, the eesmart-d2l service). Any such device publishes a value to a MQTT topic; HA subscribes to that topic via MQTT Discovery or manual config; a `sensor.*` entity is created; it fires `state_changed` on the Event Bus; InfluxDB writes it. Once the entity exists in the State Machine, the path to InfluxDB is identical to the ZHA path.

---

### 4.2 InfluxDB — `172.18.4.3:8086`

**Image:** `influxdb:2.7.1`
**Volume:** `influxdb-data` mounted at `/var/lib/influxdb2`
**Role:** Time-series database. The permanent store of all energy measurements.

InfluxDB 2.x organises data in a hierarchy:

```
Organisation: hems
  └── Bucket: home_assistant
        └── Measurements (e.g. "sensor")
              └── Series (tagged by entity_id, unit, etc.)
                    └── Points (timestamp + value)
```

Every energy reading from the Frient lands here as a point. A point contains:
- **Measurement:** always `sensor` (the HA domain name)
- **Tags:** `entity_id`, `domain`, `friendly_name`, `unit_of_measurement` — indexed, used for filtering
- **Field:** `value` — the numeric measurement
- **Timestamp:** nanosecond precision Unix time

**Example stored point:**
```
sensor,entity_id=frient_power,domain=sensor,unit_of_measurement=W value=342.0 1708789012000000000
```

InfluxDB is queried using **Flux**, a functional query language. Example to get the last hour of power readings:
```flux
from(bucket: "home_assistant")
  |> range(start: -1h)
  |> filter(fn: (r) => r.entity_id == "frient_power")
```

InfluxDB has no custom init scripts in this project. The organisation and bucket are created on first startup by InfluxDB itself using environment variables, or during initial setup through the web UI at `/influx/`.

---

### 4.3 MQTT Broker (Mosquitto) — `172.18.4.7:1883`

**Image:** `eclipse-mosquitto`
**Volume:** `mosquitto` mounted at `/mosquitto/data`
**Role:** Message broker for non-Zigbee MQTT devices.

Full configuration from `mosquitto.conf`:

```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
```

Mosquitto is a **post box with no intelligence**. It holds no application logic. Its only job is: if a client sends a message addressed to topic X, forward it to every client that has subscribed to topic X.

**`allow_anonymous true`** means any device that can reach port 1883 can publish or subscribe without a username and password. This is acceptable because the broker is on the private Docker bridge network — only containers on the bridge and WiFi devices permitted by the iptables rule in system-manager can reach it. This is a known security gap and should be hardened with credentials before production deployment.

**`persistence true`** means the broker writes its state (subscriptions and retained messages) to `/mosquitto/data/`. If the container restarts, the last retained value for every topic is replayed to new subscribers immediately, so HA does not have to wait for the next measurement to arrive before showing a value.

**Current status in this deployment:** The Frient blink counter is a Zigbee device and does not use MQTT. The broker is running but has no confirmed active Zigbee publisher. It is ready for future WiFi-based devices or the eesmart-d2l integration.

---

### 4.4 Nginx Reverse Proxy — `172.18.4.4:80`

**Image:** `arm64v8/nginx`
**Role:** Single external entry point. Routes all browser and app traffic to the correct backend service.

Without Nginx, accessing the system requires remembering three different ports:
- `:8123` for Home Assistant
- `:8086` for InfluxDB
- `:3218` for hass-configurator

With Nginx everything is on port 80 and the URL path decides the destination.

#### Startup — `start.sh`

```bash
htpasswd -cb /etc/nginx/.passwd $CONFIG_USER $CONFIG_PASSWORD
nginx
sleep infinity
```

The first line generates a basic auth credentials file from two Balena environment variables (`CONFIG_USER`, `CONFIG_PASSWORD`). This runs at container start so credentials always reflect the current environment variables. The `sleep infinity` keeps the container process alive.

#### HTTP Routing — `http.conf`

**Route 1 — Home Assistant (`/`)**

```nginx
location / {
    set $ha http://172.18.4.2:8123;
    proxy_pass $ha;
    proxy_http_version 1.1;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
}
```

Using `set $ha` as a variable rather than a literal IP in `proxy_pass` is deliberate: Nginx resolves the address at request time rather than startup. If HA is still booting when Nginx starts, a literal address would cause Nginx itself to fail. With a variable it defers resolution and starts cleanly.

The `Upgrade` and `Connection` headers enable **WebSocket support**. The HA frontend keeps a permanent WebSocket connection open so the dashboard updates in real time when sensor values change. Without these headers Nginx would close the WebSocket handshake and the UI would be static.

**`X-Forwarded-For`** passes the real client IP through to HA. HA trusts this because `configuration.yaml` declares:
```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 172.0.0.0/8   # the entire Docker bridge range
```

**Route 2 — InfluxDB (`/influx/`)**

InfluxDB's web interface is a webpack single-page application compiled with the assumption it lives at the root path `/`. Every asset reference, API call, and link inside the compiled HTML/CSS/JS uses paths like `/api/v2/query` and `src="/app.js"`. When served under `/influx/`, the browser requests `/influx/` but the HTML tells it to load `/app.js` — not `/influx/app.js` — and gets a 404.

Nginx fixes this with `sub_filter` directives that **rewrite the HTML and JavaScript on the fly** before they reach the browser:

```nginx
rewrite ^/influx/(.*) /$1 break;   # strip /influx/ before passing upstream

sub_filter '<base href="/">'  '<base href="/influx/">';
sub_filter 'src="/'           'src="/influx/';
sub_filter 'href="/'          'href="/influx/';
sub_filter '/api/'            '/influx/api/';
sub_filter 'api/v2/query'     'influx/api/v2/query';
sub_filter_once off;

proxy_pass http://172.18.4.3:8086;
```

It also injects a synthetic JavaScript file:

```nginx
location = /influx/env.js {
    return 200 "var prefix='/influx/'; process = {'env' : {'BASE_PATH': prefix, 'API_BASE_PATH': prefix}};";
}
```

This sets webpack's `process.env` at runtime so the compiled JS bundle knows its own base path. It is injected into every HTML page via:
```nginx
sub_filter '</head>' '<script src="/influx/env.js"></script></head>';
```

InfluxDB itself never knows it is being served from a subpath — it always thinks it is at `/`.

**Route 3 — hass-configurator (`/configurator/`)**

```nginx
location /configurator/ {
    set $configurator http://172.18.4.6:3218;
    proxy_pass $configurator;
    proxy_hide_header Authorization;
}
```

`proxy_hide_header Authorization` strips the basic auth header before forwarding, preventing the configurator from misinterpreting credentials intended for Nginx.

#### TCP Stream Proxy — `nginx.conf`

```nginx
stream {
    server {
        resolver 127.0.0.11 ipv6=off valid=10s;
        listen 7845;
        set $eesmart eesmart-d2l:7845;
        proxy_pass $eesmart;
        proxy_connect_timeout 1s;
        proxy_timeout 5m;
    }
}
```

This is separate from the HTTP block. The `stream` module forwards raw TCP bytes — no HTTP parsing. Port 7845 is proxied verbatim to a service named `eesmart-d2l`. The `resolver 127.0.0.11` is Docker's internal DNS server, used to resolve the container name dynamically. **Note:** No container named `eesmart-d2l` is defined in `docker-compose.yml`. This proxy route is currently broken and requires either a new container definition or an external hostname to be configured.

---

### 4.5 hass-configurator — `172.18.4.6:3218`

**Image:** `causticlab/hass-configurator-docker:latest`
**Volume:** `hass-config` mounted at `/hass-config` (shared with Home Assistant)
**Role:** In-field browser-based file editor for Home Assistant configuration.

#### The problem it solves

This system runs HA inside a plain Docker container on BalenaOS. This is fundamentally different from a Home Assistant OS (HAOS) installation. HA add-ons — including the built-in file editor and SSH terminal add-ons — **only work on HAOS/Supervised installs**. They are not available here.

The HA configuration files (`configuration.yaml`, `automations.yaml`, `scripts.yaml`, etc.) are stored inside the `hass-config` Docker volume mounted at `/config` in the HA container. Without hass-configurator, editing these files on a deployed Pi requires either:
- Running `balena ssh <device-uuid>` and editing with a terminal text editor
- Editing the files locally and running `balena push` — which triggers a full Docker image rebuild and container restart

Neither is workable for small on-site adjustments, particularly at remote pilot sites.

#### How it works

The configurator mounts the **same Docker volume** as Home Assistant:

```yaml
homeassistant:
  volumes:
    - 'hass-config:/config'      # HA sees files here

hass-configurator:
  volumes:
    - 'hass-config:/hass-config' # Configurator sees same files here
  environment:
    - HC_BASEPATH=/hass-config
```

Because it is the same volume, a file saved in the configurator's web editor is immediately visible to the HA process. After saving `configuration.yaml` the relevant HA integration can be reloaded from the HA Developer Tools without a full container restart.

#### The missing template_sensors directory

`configuration.yaml` line 32 contains:
```yaml
template: !include_dir_merge_list template_sensors/
```

This directory **does not exist in the git repository**. It is intended to be created on the deployed device via hass-configurator once real Zigbee devices are paired and their entity IDs are known. A field engineer would:

1. Pair the Frient via ZHA — HA creates `sensor.frient_smszb_120_instantaneous_demand`
2. Open `http://<pi-ip>/configurator/`
3. Create `template_sensors/energy.yaml` with a cleaner derived entity
4. Reload the template platform in HA — new sensor appears immediately, no redeployment needed

This is the primary intended workflow for device-specific configuration.

---

### 4.6 led-status — `172.18.4.9`

**Base image:** Ubuntu + `gpiozero` + `lgpio`
**Devices:** `/dev/gpiochip0` (GPIO chip passthrough)
**Volume:** `hass-config` (shared with HA, for log access)
**Role:** Physical health indicators via three GPIO-controlled LEDs on the Pi.

`led-status` is on the **bridge network** (not the host network) at `172.18.4.9`. This is necessary because it pings Home Assistant at `172.18.4.2` — an address that only exists inside the bridge network.

From `led.py`:
```python
error_led = LED(22)   # GPIO pin 22
led1      = LED(17)   # GPIO pin 17
led2      = LED(27)   # GPIO pin 27

HA_IP     = "172.18.4.2"
Google_IP = "8.8.8.8"
LOG_FILE  = "/hass-config/home-assistant.log"
```

Every two seconds, three independent checks run:

**GPIO 17 — Home Assistant health:**
```python
if ping(HA_IP):
    led1.on()
else:
    led1.off()
```
Sends a single ICMP ping to `172.18.4.2`. If HA responds, LED 17 is on. If HA is down, crashing, or restarting, the LED goes off immediately giving a physical indication.

**GPIO 27 — Internet (WAN) health:**
```python
if ping(Google_IP):
    led2.on()
else:
    led2.off()
```
Pings Google's DNS server at `8.8.8.8`. This tests whether the Pi has internet access. If the home router loses connectivity, this LED goes off independently of the HA LED.

**GPIO 22 — Home Assistant error state:**
```python
size = os.path.getsize(LOG_FILE)
if size > last_size:
    with open(LOG_FILE, "r") as f:
        f.seek(last_size)
        for line in f:
            if "ERROR" in line:
                error_led.on()
                break
        else:
            error_led.off()
    last_size = size
```
Reads only the **new lines added since the last check** (via file seek to `last_size`). If any new line contains the word `ERROR`, the error LED turns on. It only turns off when new log lines arrive with no errors. This gives a persistent physical warning when HA reports a problem.

The GPIO library requires a patch for Raspberry Pi 5 because Pi 5 uses a different GPIO chip number than Pi 4:
```python
def __patched_init(self, chip=None):
    chip = 0   # forced to chip 0 for Pi 5
    self._handle = lgpio.gpiochip_open(chip)
```

---

## 5. Host Network Services

### What "host network" means

The Docker bridge network (`172.18.4.0/24`) is isolated from the Pi's real operating system network stack. A container on the bridge cannot modify the Pi's kernel firewall rules, cannot create a WiFi access point, and cannot reach processes running directly on the Pi OS (like the Balena Supervisor) via `127.0.0.1`.

`network_mode: host` removes all network isolation for a container. It shares the Pi's actual `wlan0`, `eth0`, routing table, iptables chains, and loopback interface. It is as if the process runs directly on the Pi. The trade-off is that these containers **cannot use bridge IPs** — `172.18.4.2`, `172.18.4.3` etc. do not exist in their network namespace.

---

### 5.1 system-manager

**Base image:** Alpine 3 + Python 3
**Network:** `host`
**Volume:** `system-manager` mounted at `/data`
**Role:** Autonomous watchdog. Monitors system health and triggers corrective actions via the Balena Supervisor API.

system-manager runs two scripts.

#### iptables.sh (runs once, background)

```bash
sleep 180  # wait for NetworkManager to create its chains

iptables -I nm-sh-fw-wlan0 -o wlan0 -s 172.18.4.0/24 -d 10.42.0.0/24 -j ACCEPT
iptables-legacy -I nm-sh-fw-wlan0 -o wlan0 -s 172.18.4.0/24 -d 10.42.0.0/24 -j ACCEPT
```

The Docker bridge (`172.18.4.0/24`) is a virtual network inside the Pi. WiFi devices on a repeater subnet (`10.42.0.0/24`) are on a different interface entirely. By default, the Linux kernel blocks traffic between different interfaces. This iptables rule explicitly permits bidirectional traffic between these two networks through `wlan0`.

Without this rule, a WiFi-connected device cannot reach the MQTT broker at `172.18.4.7:1883` — the packets would be dropped at the kernel level regardless of what the broker does.

The 180-second delay is deliberate: NetworkManager creates the chain `nm-sh-fw-wlan0` after it initialises the `wlan0` interface. Inserting a rule into a non-existent chain fails with an error.

Both `iptables` and `iptables-legacy` rules are inserted because BalenaOS kernel versions may use either the nftables backend or the legacy xtables backend.

#### watchdog.py (runs permanently, every 30 seconds)

The watchdog communicates with the **Balena Supervisor**, a process running directly on the Pi OS (not in any container) that manages the Docker containers on behalf of Balena Cloud. It exposes a local HTTP REST API at the address injected into the container as `BALENA_SUPERVISOR_ADDRESS`.

```python
# constants.py — URLs built from injected environment variables
SUPERVISOR_ADDRESS = os.getenv("BALENA_SUPERVISOR_ADDRESS")
API_KEY            = os.getenv("BALENA_SUPERVISOR_API_KEY")
APP_ID             = os.getenv("BALENA_APP_ID")

STATUS_URL  = SUPERVISOR_ADDRESS + "/v2/state/status?apikey=" + API_KEY
RESTART_URL = SUPERVISOR_ADDRESS + "/v2/applications/" + APP_ID + "/restart-service?apikey=" + API_KEY
REBOOT_URL  = SUPERVISOR_ADDRESS + "/v1/reboot?apikey=" + API_KEY
```

**Check 1 — WiFi repeater lifecycle:**
```python
for container in response.json()["containers"]:
    if container["serviceName"] == "wifi-repeater":
        wifi_running = container["status"] == "Running"

if wifi_running and delta_t > timedelta(minutes=10):
    requests.post(STOP_URL, data='{"serviceName": "wifi-repeater"}')
```
Queries the Supervisor for container status. If the `wifi-repeater` service has been running for more than 10 minutes, it sends a stop command. The WiFi repeater is only needed for initial onboarding; leaving it running wastes resources.

**Check 2 — Home Assistant version guard:**
```python
version = response.json()[APP_NAME]["services"]["homeassistant"]["releaseId"]

with open("/data/version.txt", "a+") as f:
    version_old = f.read()
    if version_old != str(version):
        if datetime.now() - version_changed_at < timedelta(minutes=2):
            return version_changed_at  # grace period, don't restart yet
        requests.post(RESTART_URL, data='{"serviceName": "homeassistant"}')
        f.write(str(version))
```
Compares the current `releaseId` from Balena with the last known version stored in `/data/version.txt`. If they differ, it waits 2 minutes (grace period to allow the new image to fully download) then restarts HA via the Supervisor API. The new version is then saved. This is how OTA updates propagate — Balena pushes a new image and system-manager restarts HA to apply it without any manual SSH.

**Check 3 — Internet watchdog:**
```python
response = os.system("ping -c 1 google.com")
if response != 0:
    if datetime.now() - last_seen > timedelta(minutes=30):
        requests.post(REBOOT_URL)
```
Pings `google.com` from the host network — a true test of WAN connectivity over the real `wlan0` interface. If internet has been unreachable for 30 consecutive minutes, the entire Pi is rebooted via the Supervisor API. All reboots and restarts are logged to `/data/restartLog.txt` with timestamps.

---

### 5.2 wifi-connect

**Base image:** Debian + Balena wifi-connect binary v4.11.84
**Network:** `host`
**Capabilities:** `NET_ADMIN`
**Role:** Zero-touch WiFi onboarding via a browser-based captive portal.

When a Pi is deployed at a new pilot site, it has no WiFi credentials. `wifi-connect` solves the cold-start problem.

#### start.sh logic

```bash
export DBUS_SYSTEM_BUS_ADDRESS=unix:path=/host/run/dbus/system_bus_socket

iwgetid -r      # query the kernel for current WiFi SSID

if [ $? -eq 0 ]; then
    printf 'Skipping WiFi Connect\n'
else
    printf 'Starting WiFi Connect\n'
    ./wifi-connect
fi

sleep infinity
```

`iwgetid -r` queries the kernel wireless subsystem. It returns exit code 0 and the SSID string if the device is associated with a WiFi network. It returns exit code 1 and empty output if not. This must run from the host network — from a bridge container it would query the container's own non-existent WiFi interface.

When WiFi is not configured, `wifi-connect` turns the Pi into a **temporary WiFi access point**. A user connects to that hotspot on their phone, is redirected to a captive portal web page, selects the home WiFi network, enters the password, and the Pi connects. The access point then disappears.

**D-Bus access** (`io.balena.features.dbus: '1'`): `wifi-connect` controls WiFi by sending commands to **NetworkManager** via D-Bus, a system inter-process communication bus. D-Bus uses a Unix socket file on the host filesystem. The label tells BalenaOS to mount the host's D-Bus socket into the container so `wifi-connect` can reach NetworkManager.

**`NET_ADMIN` capability** grants permission to bring network interfaces up and down, assign IP addresses, and create virtual interfaces — all required to create and destroy the temporary access point.

---

## 6. Balena Cloud

**Role:** Remote deployment, OTA update delivery, and fleet management.

Balena Cloud is not a component that runs on the Pi. It is a cloud service that BalenaOS connects to via a persistent VPN tunnel. It provides:

- **Fleet management:** All Pi devices at pilot sites belong to the `SmartCORE` fleet. A `balena push SmartCORE` command from a developer's machine builds new Docker images and sends them to every device in the fleet simultaneously.
- **Environment variable injection:** The variables `INFLUX_TOKEN`, `CONFIG_USER`, `CONFIG_PASSWORD`, `BALENA_APP_NAME`, and `BALENA_APP_ID` are set in the Balena dashboard. BalenaOS injects them as environment variables into the relevant containers at startup. They never appear in the codebase.
- **Supervisor API:** The Balena Supervisor runs on BalenaOS itself and exposes a local HTTP API at `BALENA_SUPERVISOR_ADDRESS`. system-manager uses this API to stop, restart, and reboot services without needing direct Docker socket access.

`balena.yml` at the repo root identifies the fleet:
```yaml
name: SmartCORE
type: sw.application
data:
  defaultDeviceType: raspberrypi5
  supportedDeviceTypes:
    - raspberrypi5
version: 1.0.1
```

---

## 7. Remote Access — Mobile Application

By default, Home Assistant is only accessible on the same local network as the Pi. The HA mobile app (iOS/Android) on an external network (4G/5G) cannot reach it without one of the following configurations.

### Option A — Nabu Casa (Recommended)

Nabu Casa is the official Home Assistant Cloud service. It establishes a persistent tunnel from HA to Nabu Casa's servers that the mobile app connects through.

**Setup:** In the HA web UI, go to Settings → Home Assistant Cloud → Sign In → Enable Remote UI. In the mobile app, sign in with the same Nabu Casa account.

**Why it works here:** No port forwarding required. No code changes required. The tunnel is initiated outbound from the Pi — it works behind any NAT router.

**Trade-off:** Requires a paid Nabu Casa subscription (~$7/month). Traffic passes through Nabu Casa's infrastructure.

### Option B — Tailscale (Free)

Tailscale creates a peer-to-peer encrypted VPN between the Pi and the mobile device.

**Setup:** Add a Tailscale container to `docker-compose.yml`:
```yaml
tailscale:
  image: tailscale/tailscale:latest
  network_mode: host
  cap_add:
    - NET_ADMIN
    - SYS_MODULE
  environment:
    - TS_AUTHKEY=<your-auth-key>
```
Install Tailscale on the phone and sign in with the same account. Use the Pi's Tailscale IP (e.g. `http://100.x.x.x:80`) as the server URL in the mobile app.

**Trade-off:** Free tier is sufficient. Both the Pi and phone must have Tailscale installed. No Nabu Casa dependency.

### Option C — Direct port forwarding (Not Recommended)

Forward port 8123 on the home router to the Pi's local IP. **This is not recommended** because the current codebase has no HTTPS/TLS configured. Exposing plain HTTP HA to the internet risks credential interception and unauthorised access.

---

*Document generated from full codebase analysis and architecture review — 2026-02-24.*
*Maintained by the SmartCORE Ireland team, Munster Technological University.*
