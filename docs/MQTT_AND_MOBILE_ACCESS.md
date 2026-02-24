# SmartCORE HEMS — MQTT Broker & Mobile Access Reference

**Project:** SmartCORE Ireland — Home Energy Management System
**Organisation:** Munster Technological University (MTU)
**Scope:** This document covers two topics in full technical detail:
1. How the MQTT broker works — both in the current architecture and in the Zigbee2MQTT alternative architecture
2. Every mobile access solution available for the HA companion app, with implementation examples

---

## Table of Contents

1. [The MQTT Protocol](#1-the-mqtt-protocol)
   - [Core Concept: Publish / Subscribe](#11-core-concept-publish--subscribe)
   - [Topics](#12-topics)
   - [QoS Levels](#13-qos-levels)
   - [Retained Messages](#14-retained-messages)
2. [Mosquitto — The Broker in This Project](#2-mosquitto--the-broker-in-this-project)
   - [Current Configuration](#21-current-configuration)
   - [Persistence on Disk](#22-persistence-on-disk)
   - [Security Considerations](#23-security-considerations)
3. [Current Architecture — Broker is Idle](#3-current-architecture--broker-is-idle)
4. [Zigbee2MQTT Architecture — Broker Becomes Active](#4-zigbee2mqtt-architecture--broker-becomes-active)
   - [What Changes and Why](#41-what-changes-and-why)
   - [docker-compose.yml Changes](#42-docker-composeyml-changes)
   - [Zigbee2MQTT as MQTT Publisher](#43-zigbee2mqtt-as-mqtt-publisher)
   - [MQTT Topic Structure](#44-mqtt-topic-structure)
   - [MQTT Discovery — How HA Auto-Creates Entities](#45-mqtt-discovery--how-ha-auto-creates-entities)
   - [End-to-End Message Flow](#46-end-to-end-message-flow)
   - [Retained Messages in Practice](#47-retained-messages-in-practice)
5. [Mobile Access Solutions](#5-mobile-access-solutions)
   - [Option A — Nabu Casa (HA Cloud)](#option-a--nabu-casa-ha-cloud)
   - [Option B — Tailscale VPN](#option-b--tailscale-vpn)
   - [Option C — Cloudflare Tunnel](#option-c--cloudflare-tunnel)
   - [Comparison Table](#comparison-table)
   - [Combining Multiple Solutions](#combining-multiple-solutions)

---

## 1. The MQTT Protocol

### 1.1 Core Concept: Publish / Subscribe

MQTT (Message Queuing Telemetry Transport) is a lightweight **publish/subscribe** messaging protocol that runs over TCP. It was designed originally for low-bandwidth, high-latency networks (satellite links, GSM) and is now the dominant protocol for IoT devices.

The fundamental difference from a direct request/response model:

```
Traditional (HTTP request/response):
  Sensor ──GET/POST──► Server ──response──► Sensor
  (sensor must know the server's address, must be online simultaneously)

MQTT publish/subscribe:
  Sensor ──publish──► Broker ──forward──► Consumer
  (sensor and consumer never communicate directly)
  (consumer does not need to be online when sensor publishes)
```

Three roles:

- **Broker** — the central router. Receives every message, stores retained copies, and forwards to all matching subscribers. In this project: Mosquitto at `172.18.4.7:1883`.
- **Publisher** — any client that sends a message to a named channel (a *topic*). Does not know or care who is listening.
- **Subscriber** — any client that tells the broker "send me messages published on topic X". Does not know or care who published them.

The broker has **no application logic**. It has no knowledge of energy data, Home Assistant, or Zigbee. It only cares about topic names and which clients subscribed to them.

---

### 1.2 Topics

A topic is a UTF-8 string used as the address for a message, written like a file path using `/` as a separator:

```
zigbee2mqtt/frient_blink_counter
homeassistant/sensor/frient_blink_counter/power/config
zigbee2mqtt/bridge/state
```

There is **no pre-registration** of topics. A publisher creates a topic simply by publishing to it. A subscriber can subscribe to a topic that has never had any messages yet.

**Wildcards** — used only in subscriptions, never in publish topics:

| Wildcard | Level | Example subscription | Matches | Does not match |
|---|---|---|---|---|
| `+` | Single level | `zigbee2mqtt/+` | `zigbee2mqtt/frient` | `zigbee2mqtt/a/b` |
| `#` | Multi-level (end only) | `zigbee2mqtt/#` | `zigbee2mqtt/frient`, `zigbee2mqtt/a/b/c` | `other/topic` |
| `+/+/config` | Two single levels | `homeassistant/sensor/config` | N/A (illustrative) | — |

Home Assistant MQTT Integration uses `zigbee2mqtt/#` and `homeassistant/#` to capture everything published by Zigbee2MQTT.

---

### 1.3 QoS Levels

Every published message carries a Quality of Service level that governs delivery guarantees between publisher and broker, and between broker and subscriber.

| Level | Name | Guarantee | Mechanism |
|---|---|---|---|
| 0 | At most once | Fire and forget. No acknowledgement. Packet may be lost. | Single TCP write |
| 1 | At least once | Delivery guaranteed, but duplicates possible. | Publisher retries until broker sends PUBACK |
| 2 | Exactly once | Exactly one delivery guaranteed, no duplicates. | Four-way handshake (PUBLISH → PUBREC → PUBREL → PUBCOMP) |

Zigbee2MQTT uses:
- **QoS 0** for live sensor readings — acceptable since a reading arrives every few seconds; missing one is not critical
- **QoS 1** for bridge control messages and MQTT Discovery payloads — more important to guarantee delivery

Note: QoS is negotiated independently for publish and subscribe. A publisher can send at QoS 1 but a subscriber can receive at QoS 0 if it chooses.

---

### 1.4 Retained Messages

The **retained** flag instructs the broker to store the last message for a topic permanently. When a new subscriber connects and subscribes to that topic, the broker immediately delivers the last retained message — even if the publisher is offline.

```
Without retain:
  Z2M publishes {"power": 342}       at 09:00:00
  HA restarts                         at 09:01:00
  HA resubscribes to zigbee2mqtt/#   at 09:01:05
  → no message delivered until next Z2M publish at ~09:01:10
  → sensor.frient_blink_counter_power is "unknown" for 5 seconds

With retain:
  Z2M publishes {"power": 342, retain: true}  at 09:00:00
  Broker stores this as the retained value for the topic
  HA restarts                                  at 09:01:00
  HA resubscribes to zigbee2mqtt/#            at 09:01:05
  → broker delivers {"power": 342} immediately
  → sensor.frient_blink_counter_power = 342 before UI even loads
```

Zigbee2MQTT sets `retain: true` on all sensor readings and all MQTT Discovery payloads by default.

---

## 2. Mosquitto — The Broker in This Project

### 2.1 Current Configuration

File: `services/mqtt/mosquitto.conf`

```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
```

**`listener 1883`**
Opens a TCP socket bound to all network interfaces on port 1883. Inside the Docker bridge network this is reachable at `172.18.4.7:1883`. The `docker-compose.yml` exposes this to the Pi's host network via `ports: - "1883:1883"`, making it reachable from anything on the home LAN at `<pi-ip>:1883`.

**`allow_anonymous true`**
Any TCP client can connect without a username or password. The broker accepts all connections. This is currently acceptable because:
- The primary clients (Zigbee2MQTT, Home Assistant) are on the private Docker bridge network `172.18.4.0/24`
- WiFi devices on the repeater subnet can reach it via the iptables rule set by system-manager, but this is a controlled local network
- The broker is not exposed to the internet

This is a **known security gap** — see [Section 2.3](#23-security-considerations).

**`persistence true`** + **`persistence_location /mosquitto/data/`**
Two things are persisted to disk:
1. **Retained messages** — the last message marked `retain: true` for every topic is written to `mosquitto.db`. Survived container restart and Pi reboot.
2. **Persistent sessions** — if a client connects with `clean_session: false`, the broker saves its subscription list. Messages that arrive while the client is offline are queued and delivered when it reconnects.

The `mosquitto` Docker volume maps `/mosquitto/data/` so this survives container lifecycle:
```yaml
mqtt:
  volumes:
    - mosquitto:/mosquitto/data
```

---

### 2.2 Persistence on Disk

The data directory contains one binary file:

```
/mosquitto/data/
└── mosquitto.db    ← Berkeley DB format: retained messages + persistent sessions
```

Conceptual content of a retained message as stored:
```
topic:   zigbee2mqtt/frient_blink_counter
payload: {"power":342.0,"energy":1234.567,"linkquality":67}
qos:     0
retain:  true
```

When Mosquitto restarts, it reads this file during startup and immediately makes all retained messages available to subscribers — before any publisher reconnects.

---

### 2.3 Security Considerations

The current `allow_anonymous true` configuration means:

1. Any device on `172.18.4.0/24` (Docker bridge) can publish to any topic — including injecting false power readings into `zigbee2mqtt/frient_blink_counter`.
2. Any device on the bridge can subscribe to any topic and observe all energy data.
3. Port 1883 is published to the host, so any LAN device can also connect.

**Recommended hardening (before production deployment):**

Step 1 — Create a password file inside the `mqtt` container:
```bash
# Run inside the mosquitto container
mosquitto_passwd -c /mosquitto/config/passwd homeassistant
# Enter a strong password when prompted
```

Step 2 — Update `mosquitto.conf`:
```
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
persistence true
persistence_location /mosquitto/data/
```

Step 3 — Update the HA MQTT Integration in the HA web UI:
- Settings → Devices & Services → MQTT → Configure
- Add `username` and `password` matching the password file

Step 4 — Update Zigbee2MQTT environment variables in `docker-compose.yml`:
```yaml
environment:
  - ZIGBEE2MQTT_CONFIG_MQTT_USER=zigbee2mqtt
  - ZIGBEE2MQTT_CONFIG_MQTT_PASSWORD=<password>
```

---

## 3. Current Architecture — Broker is Idle

In the current deployment, the Zigbee data path **does not use MQTT at all**:

```
Frient SMSZB-120 (Zigbee radio)
  → Sonoff ZBDongle-E (USB serial / EZSP)
    → ZHA Integration (runs inside HA Python process)
      → HA State Machine (sensor.frient_power, sensor.frient_energy)
        → HA Event Bus (state_changed event)
          → InfluxDB Integration
            → InfluxDB (HTTP POST /api/v2/write)
```

The Mosquitto broker is running but has **no active Zigbee publisher**. The broker starts, opens port 1883, and waits. The HA MQTT Integration is connected to it and subscribed, but no messages arrive because no device is publishing. The broker is infrastructure held in reserve for:

- Future WiFi-based devices (ESP32 sensors, Shelly relays) that speak native MQTT
- The eesmart-d2l service integration (the TCP stream proxy exists but the container is not yet defined)
- The alternative Zigbee2MQTT architecture described in Section 4

---

## 4. Zigbee2MQTT Architecture — Broker Becomes Active

### 4.1 What Changes and Why

Zigbee2MQTT (`Z2M`) is an open-source project that acts as a **bridge between the Zigbee radio network and MQTT**. It replaces ZHA as the Zigbee coordinator stack. The key difference:

| | ZHA (current) | Zigbee2MQTT |
|---|---|---|
| USB dongle owner | `homeassistant` container | `zigbee2mqtt` container |
| Protocol to dongle | EZSP (inside HA process) | EZSP (inside Z2M process) |
| Output | Internal HA entity update | MQTT message to broker |
| HA integration | ZHA (built-in) | MQTT Integration + Discovery |
| MQTT broker role | Idle | Active message bus |
| Broker dependency | None | Required |

**Why choose Zigbee2MQTT over ZHA?**
- Z2M supports more devices and updates its device database faster than ZHA
- All device data is accessible to any MQTT subscriber, not just HA — easier to integrate external systems (Node-RED, custom scripts, cloud connectors)
- The MQTT layer makes the architecture more loosely coupled — HA can be replaced or restarted without losing Zigbee coordinator state
- Explicit, inspectable MQTT messages make debugging easier (can use `mosquitto_sub` to watch raw data)

---

### 4.2 docker-compose.yml Changes

**1. Add the `zigbee2mqtt` service:**
```yaml
zigbee2mqtt:
  image: koenkk/zigbee2mqtt:latest
  privileged: true
  devices:
    - "/dev/ttyUSB0:/dev/ttyUSB0"    # Sonoff ZBDongle-E serial port
  volumes:
    - zigbee2mqtt-data:/app/data     # config, coordinator_backup.json, database.db
  depends_on:
    - mqtt
  networks:
    hems:
      ipv4_address: 172.18.4.5
  restart: always
  environment:
    - ZIGBEE2MQTT_CONFIG_MQTT_SERVER=mqtt://172.18.4.7
    - ZIGBEE2MQTT_CONFIG_MQTT_BASE_TOPIC=zigbee2mqtt
    - ZIGBEE2MQTT_CONFIG_HOMEASSISTANT=true          # enables MQTT Discovery
    - ZIGBEE2MQTT_CONFIG_SERIAL_PORT=/dev/ttyUSB0
    - ZIGBEE2MQTT_CONFIG_SERIAL_ADAPTER=ezsp         # EFR32MG21 chip type
    - ZIGBEE2MQTT_CONFIG_PERMIT_JOIN=false           # disable pairing after initial setup
```

**2. Remove the USB device passthrough from `homeassistant`:**
The USB dongle can only be owned by one process at a time. Move it from HA to Z2M:
```yaml
homeassistant:
  privileged: false    # no longer needs raw USB access
  # Remove: devices section (no longer needed)
```

**3. Add the new volume:**
```yaml
volumes:
  hass-config:
  mosquitto:
  influxdb-data:
  system-manager:
  zigbee2mqtt-data:    # NEW
```

**4. Remove ZHA integration from HA:**
In the HA web UI: Settings → Devices & Services → Zigbee Home Automation → Delete.
The MQTT Integration (already configured at `172.18.4.7:1883`) becomes the primary sensor source.

---

### 4.3 Zigbee2MQTT as MQTT Publisher

When the `zigbee2mqtt` container starts, it performs the following sequence:

```
1. Opens /dev/ttyUSB0 serial connection to Sonoff ZBDongle-E
2. Loads its device database from /app/data/database.db
   (paired devices are remembered here across restarts)
3. Connects to Mosquitto at mqtt://172.18.4.7 with client_id=zigbee2mqtt
4. Publishes (retained): zigbee2mqtt/bridge/state = "online"
5. Publishes (retained): zigbee2mqtt/bridge/devices = [JSON array of all paired devices]
6. Publishes (retained): homeassistant/sensor/<device>/<field>/config
   (MQTT Discovery config for every known device — HA reads this to create entities)
7. Begins polling Zigbee radio
8. On every Frient measurement: decode → JSON → PUBLISH zigbee2mqtt/frient_blink_counter
```

The Z2M configuration is stored in `/app/data/configuration.yaml` inside the container (different from HA's `configuration.yaml`). Environment variables in `docker-compose.yml` override its values — this is the recommended pattern for containerised deployments so config lives in Balena env vars, not inside the image.

---

### 4.4 MQTT Topic Structure

All topics published by Zigbee2MQTT for the Frient SMSZB-120:

**Live sensor data** — published every ~10 seconds (configurable):

```
Topic:   zigbee2mqtt/frient_blink_counter
Retain:  true
QoS:     0
Payload:
{
  "power": 342.0,
  "energy": 1234.567,
  "linkquality": 67,
  "voltage": 3.0,
  "battery": 89
}
```

**Bridge health** — published on startup and on state change:

```
Topic:   zigbee2mqtt/bridge/state
Retain:  true
Payload: "online"   (or "offline" when Z2M shuts down cleanly)
```

**Device registry** — published on startup:

```
Topic:   zigbee2mqtt/bridge/devices
Retain:  true
Payload: [
  {
    "ieee_address": "0x0015bc003f000001",
    "friendly_name": "frient_blink_counter",
    "type": "EndDevice",
    "model": "SMSZB-120",
    "manufacturer": "Frient",
    "supported": true
  }
]
```

**MQTT Discovery configs** — published on startup, one per sensor field:

```
Topic:   homeassistant/sensor/frient_blink_counter/power/config
Retain:  true
Payload: {see Section 4.5}

Topic:   homeassistant/sensor/frient_blink_counter/energy/config
Retain:  true
Payload: {see Section 4.5}

Topic:   homeassistant/sensor/frient_blink_counter/linkquality/config
Retain:  true
Payload: {see Section 4.5}
```

**Device availability** — published when the Frient goes online/offline:

```
Topic:   zigbee2mqtt/frient_blink_counter/availability
Retain:  true
Payload: "online" or "offline"
```

---

### 4.5 MQTT Discovery — How HA Auto-Creates Entities

MQTT Discovery is the mechanism by which Zigbee2MQTT tells Home Assistant to create and configure sensor entities — **without any changes to `configuration.yaml`**.

The discovery topic format is:
```
homeassistant/<component>/<node_id>/<object_id>/config
```

Z2M publishes one discovery payload per sensor field. Here are the two most important:

**Power sensor discovery:**
```
Topic: homeassistant/sensor/frient_blink_counter/power/config
```
```json
{
  "name": "Frient Blink Counter Power",
  "state_topic": "zigbee2mqtt/frient_blink_counter",
  "value_template": "{{ value_json.power }}",
  "unit_of_measurement": "W",
  "device_class": "power",
  "state_class": "measurement",
  "unique_id": "0x0015bc003f000001_power_zigbee2mqtt",
  "availability_topic": "zigbee2mqtt/frient_blink_counter/availability",
  "device": {
    "identifiers": ["zigbee2mqtt_0x0015bc003f000001"],
    "name": "Frient Blink Counter",
    "model": "SMSZB-120",
    "manufacturer": "Frient",
    "sw_version": "20230413"
  }
}
```

**Energy sensor discovery:**
```
Topic: homeassistant/sensor/frient_blink_counter/energy/config
```
```json
{
  "name": "Frient Blink Counter Energy",
  "state_topic": "zigbee2mqtt/frient_blink_counter",
  "value_template": "{{ value_json.energy }}",
  "unit_of_measurement": "kWh",
  "device_class": "energy",
  "state_class": "total_increasing",
  "unique_id": "0x0015bc003f000001_energy_zigbee2mqtt",
  "availability_topic": "zigbee2mqtt/frient_blink_counter/availability",
  "device": {
    "identifiers": ["zigbee2mqtt_0x0015bc003f000001"],
    "name": "Frient Blink Counter",
    "model": "SMSZB-120",
    "manufacturer": "Frient"
  }
}
```

**What Home Assistant does with each discovery payload:**

```
1. HA MQTT Integration is subscribed to: homeassistant/#

2. Mosquitto delivers the power sensor discovery config
   (retained — delivered immediately on subscribe)

3. HA parses the JSON:
   - name:             "Frient Blink Counter Power"
   - state_topic:      "zigbee2mqtt/frient_blink_counter"
   - value_template:   "{{ value_json.power }}"
   - unit:             "W"
   - device_class:     "power"

4. HA creates entity: sensor.frient_blink_counter_power
   Registers it in the entity registry (written to hass-config volume)
   Subscribes to state_topic: "zigbee2mqtt/frient_blink_counter"

5. Next live payload arrives from Z2M:
   {"power": 342.0, "energy": 1234.567, "linkquality": 67}

6. HA evaluates value_template:
   {{ value_json.power }} → 342.0

7. HA sets sensor.frient_blink_counter_power state = "342.0", unit = "W"

8. HA Event Bus fires:
   state_changed {
     entity_id: "sensor.frient_blink_counter_power",
     new_state: { state: "342.0", attributes: { unit_of_measurement: "W" } }
   }

9. InfluxDB Integration receives state_changed (domain = "sensor", passes filter)

10. HTTP POST to InfluxDB:
    sensor,entity_id=frient_blink_counter_power,unit_of_measurement=W value=342.0 <timestamp>
```

No `configuration.yaml` editing. No HA restart. The entity appears in the HA UI within seconds of Z2M starting up.

---

### 4.6 End-to-End Message Flow

```
Smart Meter S0 LED pulses at rate proportional to power draw
│
│  [S0 pulse wire, physical connection]
▼
Frient SMSZB-120 (SMSZB-120)
│  Counts pulses
│  Calculates: power = 3,600,000 / (pulse_interval_ms × 1000) W
│              energy = pulse_count / 1000 kWh
│
│  [IEEE 802.15.4 Zigbee radio, 2.4 GHz]
▼
Sonoff ZBDongle-E (/dev/ttyUSB0)
│  Receives Zigbee frame via radio
│
│  [USB serial, EZSP protocol binary frames]
▼
zigbee2mqtt container (172.18.4.5)
│  Decodes EZSP → Zigbee cluster 0x0702 (Smart Energy Metering)
│  Extracts: power=342.0W, energy=1234.567kWh, linkquality=67
│  Formats JSON payload
│
│  [TCP connect to 172.18.4.7:1883]
│  MQTT PUBLISH:
│    topic:   zigbee2mqtt/frient_blink_counter
│    payload: {"power":342.0,"energy":1234.567,"linkquality":67}
│    QoS:     0
│    retain:  true
▼
Mosquitto MQTT Broker (172.18.4.7:1883)
│  Stores retained copy in mosquitto.db
│  Looks up all subscribers matching zigbee2mqtt/#
│  Delivers to each subscriber
│
│  [TCP delivery to HA MQTT Integration client]
▼
HA MQTT Integration (inside homeassistant container 172.18.4.2)
│  Subscribed to: zigbee2mqtt/# and homeassistant/#
│  Receives payload
│  Matches to entity: sensor.frient_blink_counter_power
│  Applies value_template: {{ value_json.power }} → 342.0
│  Updates entity state
│
▼
HA State Machine
│  sensor.frient_blink_counter_power = 342.0 W (updated)
│  sensor.frient_blink_counter_energy = 1234.567 kWh (updated)
│
▼
HA Internal Event Bus
│  Fires: state_changed(entity_id=sensor.frient_blink_counter_power, new=342.0)
│  Fires: state_changed(entity_id=sensor.frient_blink_counter_energy, new=1234.567)
│
├──► HA Dashboard: updates live UI in browser/app
│
└──► InfluxDB Integration
     │  entity domain = "sensor" → passes include filter
     │  Formats line protocol:
     │    sensor,entity_id=frient_blink_counter_power value=342.0 1708789012000000000
     │
     │  [HTTP POST to 172.18.4.3:8086]
     │  Authorization: Token <INFLUX_TOKEN>
     │  /api/v2/write?org=hems&bucket=home_assistant
     ▼
     InfluxDB 2.7 (172.18.4.3:8086)
       Writes point to bucket: home_assistant
       Measurement: sensor
       Tags: entity_id=frient_blink_counter_power, unit_of_measurement=W
       Field: value=342.0
       Timestamp: <nanoseconds>
```

---

### 4.7 Retained Messages in Practice

Consider what happens when HA restarts (common after OTA updates):

**Without retention (hypothetical):**
```
09:00:00  Z2M publishes power=342W (no retain)
09:01:00  HA restarts (system-manager watchdog trigger)
09:01:05  HA MQTT Integration reconnects, subscribes
09:01:05  → No message delivered (last message was at 09:00:00, not retained)
09:01:10  Z2M publishes next reading: power=338W
09:01:10  → sensor.frient_blink_counter_power = 338 ← 5 second gap of "unknown"
```

**With retention (actual Z2M behaviour):**
```
09:00:00  Z2M publishes power=342W (retain=true)
09:00:00  Mosquitto stores: topic=zigbee2mqtt/frient_blink_counter, payload={power:342,...}
09:01:00  HA restarts
09:01:05  HA MQTT Integration reconnects, subscribes to zigbee2mqtt/#
09:01:05  Mosquitto IMMEDIATELY delivers retained: {power:342,...}
09:01:05  → sensor.frient_blink_counter_power = 342 ← no gap at all
09:01:10  Z2M publishes next reading normally
```

Discovery payloads benefit even more from retention. They are published once by Z2M at startup, stored as retained by the broker, and replayed to HA every time HA reconnects — even if Z2M is currently offline. This means HA can reconstruct all entity definitions from the broker's memory without Z2M being running.

---

## 5. Mobile Access Solutions

### Overview

By default, the HEMS web interface and the Home Assistant companion app are only reachable on the **local network where the Pi is connected** (e.g. `http://192.168.1.50:80`). From a mobile device on 4G or a different WiFi network, that address is unreachable.

The HA companion app (iOS/Android) supports two configured URLs:
- **Internal URL** — used when on the home WiFi (detected by WiFi SSID)
- **External URL** — used when away from home

The challenge: getting the external URL to reach a Pi behind a home NAT router without exposing it unsafely to the internet.

---

### Option A — Nabu Casa (HA Cloud)

#### What it is

Nabu Casa is the company behind Home Assistant. They offer a subscription cloud service called **HA Cloud** that provides a secure relay tunnel purpose-built for HA. It is the only option that requires **zero changes to the codebase** — setup is entirely within the HA web UI.

#### How it works technically

```
Home Assistant container (172.18.4.2)
  │
  │  [On startup, HA establishes an outbound persistent WebSocket]
  │  wss://cloud.nabucasa.com/ws
  ▼
Nabu Casa Cloud (cloud.nabucasa.com)
  │  Holds the open connection
  │  Assigns URL: https://your-unique-id.ui.nabu.casa
  │
  [Mobile request arrives at Nabu Casa]
  │
Mobile App → GET https://your-id.ui.nabu.casa/api/states
  │
  ▼
Nabu Casa receives request
  Tunnels it down the already-open WebSocket connection to the Pi
  │
  ▼
Home Assistant at 172.18.4.2:8123 handles the request
Response travels back up the WebSocket to the mobile app
```

The Pi initiates the outbound connection — no inbound ports are needed. Works behind any NAT, behind Carrier-Grade NAT, on any ISP.

#### HA API over the tunnel

The HA companion app communicates using:

```
HTTPS GET  https://your-id.ui.nabu.casa/api/states
→ Returns all entity states as JSON array

HTTPS POST https://your-id.ui.nabu.casa/api/services/switch/turn_on
→ Triggers a service call

WSS wss://your-id.ui.nabu.casa/api/websocket
→ Persistent WebSocket for real-time events
→ App receives state_changed events live as sensors update
→ Dashboard updates in real time without polling
```

#### Setup steps

1. In HA web UI: **Settings → Home Assistant Cloud**
2. Create a Nabu Casa account (or sign in)
3. Enable **Remote UI** toggle
4. HA generates your unique URL (e.g. `https://abc123.ui.nabu.casa`)
5. Install the HA companion app on iOS/Android
6. Open app → **Add Home** → enter the Nabu Casa URL
7. Sign in with your HA credentials

No `docker-compose.yml` changes. No Nginx changes. No firewall changes.

#### Trade-offs

| Pros | Cons |
|---|---|
| Zero configuration — works immediately | ~£6/month subscription |
| Built into HA — officially supported | All traffic routes through Nabu Casa's servers |
| Automatic TLS, no cert management | US-based servers — adds latency from Ireland |
| Works on any network, any ISP | If Nabu Casa has an outage, remote access is lost |
| No client software on phone | —  |

---

### Option B — Tailscale VPN

#### What it is

Tailscale is a **WireGuard-based mesh VPN**. WireGuard is a modern VPN protocol built into the Linux kernel (since 5.6) that uses Curve25519 key exchange, ChaCha20 encryption, and Poly1305 authentication. Tailscale wraps WireGuard with automatic key management, NAT traversal, and a coordination server that handles device registration.

The critical distinction from traditional VPNs: there is **no central server that all data flows through**. Tailscale attempts to establish a **direct peer-to-peer encrypted tunnel** between devices. Tailscale's coordination servers only facilitate the initial handshake — after that, packets flow directly between the Pi and the phone.

#### How WireGuard NAT traversal works

```
Phone (behind 4G carrier NAT)     Pi (behind home router NAT)
  Tailscale IP: 100.64.20.3         Tailscale IP: 100.64.10.5
  Public endpoint: 5.6.7.8:49231    Public endpoint: 1.2.3.4:41641

1. Both devices contact Tailscale coordination server
2. Coordination server shares each device's WireGuard public key
   and current public endpoint (IP:port determined by STUN)
3. Phone and Pi simultaneously send UDP packets to each other's
   public endpoints (UDP hole-punching)
4. Home router sees outbound UDP from Pi to phone → creates NAT mapping
5. Phone's UDP packet arrives at home router → matches mapping → forwarded to Pi
6. Direct WireGuard tunnel established

If NAT types are incompatible (symmetric NAT on both sides):
→ Traffic relays through Tailscale DERP servers (Designated Encrypted Relay for Packets)
→ Still encrypted end-to-end with WireGuard — DERP sees only ciphertext
→ Slightly higher latency but still secure
```

#### New container at `172.18.4.10`

```yaml
tailscale:
  image: tailscale/tailscale:latest
  hostname: hems-pi
  environment:
    - TS_AUTHKEY=${TS_AUTH_KEY}           # one-time auth key from Tailscale dashboard
    - TS_EXTRA_ARGS=--advertise-routes=172.18.4.0/24
    - TS_STATE_DIR=/var/lib/tailscale
  volumes:
    - tailscale-state:/var/lib/tailscale  # persists WireGuard keys + auth state
  devices:
    - /dev/net/tun:/dev/net/tun           # TUN device for kernel WireGuard interface
  cap_add:
    - NET_ADMIN      # create network interfaces, modify routing tables
    - SYS_MODULE     # load kernel modules (wireguard.ko if not built-in)
  networks:
    hems:
      ipv4_address: 172.18.4.10
  restart: always
```

Add to the `volumes` section:
```yaml
volumes:
  tailscale-state:
```

**`TS_AUTHKEY`** — a reusable auth key generated in the Tailscale admin console at `login.tailscale.com`. Set as `TS_AUTH_KEY` in Balena Cloud environment variables. Never stored in the git repository. On first boot the container uses this key to register with the Tailscale network. After registration, auth state is saved to the `tailscale-state` volume and the key is no longer needed for subsequent restarts.

**`--advertise-routes=172.18.4.0/24`** — tells Tailscale "I can route packets to the entire `172.18.4.0/24` subnet". After you approve this in the Tailscale admin console, phones in the Tailscale network will route any `172.18.4.x` destination through the tunnel to the Pi's Tailscale container. This means the phone can reach Nginx at `172.18.4.4:80` directly.

**`/dev/net/tun`** — the Linux TUN/TAP kernel interface. WireGuard creates a virtual network adapter (`tailscale0`) through this device. Packets sent to `tailscale0` are encrypted and sent as UDP; packets received are decrypted and injected as if from a local interface.

**`tailscale-state` volume** — stores the WireGuard private key and authentication tokens. Without this, the container generates a new keypair on every restart and must re-authenticate with the coordination server.

#### Full access flow from phone

```
User opens HA companion app on phone (4G, no home WiFi)
│
│  App detects: not on home SSID → use External URL: http://100.64.10.5:80
│
▼
Tailscale app running on phone
│  Has established WireGuard virtual interface (utun3 on iOS)
│  Routing table: 100.64.10.5 via utun3 (direct WireGuard)
│                 172.18.4.0/24 via 100.64.10.5 (advertised route, if approved)
│
│  [WireGuard UDP encrypted packets, direct P2P or via DERP]
▼
Tailscale container on Pi (Tailscale IP: 100.64.10.5, bridge IP: 172.18.4.10)
│  WireGuard decrypts inbound packets
│  Destination: 172.18.4.10 port 80? Actually this hits the Pi's Nginx
│  (The Pi's kernel routes 172.18.4.4 via the bridge interface)
│
│  [Docker bridge network, plain TCP]
▼
Nginx reverse proxy (172.18.4.4:80)
│  Receives HTTP request
│  X-Forwarded-For: 100.64.20.3 (Tailscale IP of phone)
│  Routes to: http://172.18.4.2:8123 (HA)
│
▼
Home Assistant → response follows the same path in reverse
```

#### HA companion app configuration

The app supports automatic internal/external URL switching:
- **Internal URL:** `http://192.168.1.50:80` — used when phone SSID matches home network
- **External URL:** `http://100.64.10.5:80` — used on 4G or other networks

The Pi's Tailscale IP (`100.64.10.5`) is stable and never changes as long as the same auth state is maintained.

#### One-time Tailscale admin console tasks

1. Go to `login.tailscale.com` → Machines tab
2. After the `tailscale` container starts, `hems-pi` appears in the list
3. Click `hems-pi` → Edit Route Settings → **Approve** route `172.18.4.0/24`
4. (Optional) Disable key expiry for `hems-pi` — prevents forced re-auth every 90 days

This approval is done once. It persists in the Tailscale account.

#### Trade-offs

| Pros | Cons |
|---|---|
| Free tier is sufficient | Tailscale app must be installed on every phone |
| No port forwarding | All phones must be in the same Tailscale account |
| Direct P2P — lowest latency of all options | Slightly more complex initial setup |
| End-to-end WireGuard encryption | `tailscale-state` volume loss = re-auth required |
| Works on any network, including CGNAT | — |
| Pi's real IP is never exposed | — |

---

### Option C — Cloudflare Tunnel

#### What it is

Cloudflare Tunnel (formerly Argo Tunnel) creates an **outbound-only HTTPS connection from the Pi to Cloudflare's global edge network**. Cloudflare's CDN then routes a public HTTPS URL (`https://hems.yourdomain.com`) through that tunnel to your local Nginx — without any inbound ports, no public IP requirement, and no client software on the accessing device.

Unlike Tailscale (which creates a private mesh), Cloudflare Tunnel creates a **public HTTPS URL**. Anyone with the URL can attempt to reach the login page (HA authentication provides the first layer of protection; Cloudflare Access can add a second layer — see below).

#### How it works technically

The `cloudflared` daemon makes **four outbound HTTPS connections** to Cloudflare edge Points of Presence (PoPs) simultaneously for redundancy:

```
cloudflared container (172.18.4.11)
  │
  │  [4 × outbound HTTPS connections to Cloudflare edge PoPs]
  │  Connects to: region1.argotunnel.com, region2.argotunnel.com, ...
  │  Uses: HTTP/2, long-lived connections, no inbound port required
  ▼
Cloudflare Edge (nearest PoP — e.g. Dublin CF PoP)
  │  Registers tunnel ID from TUNNEL_TOKEN
  │  Maps: hems.yourdomain.com → this tunnel
  │  Issues TLS certificate for hems.yourdomain.com (Cloudflare CA)
  │  Holds 4 open connections to cloudflared
```

When a user accesses `https://hems.yourdomain.com`:

```
Mobile App → DNS lookup: hems.yourdomain.com
  │
  │  [Cloudflare DNS returns Cloudflare anycast IP — NOT the Pi's IP]
  ▼
Cloudflare Edge (Dublin PoP)
  │  TLS terminates here (Cloudflare-managed certificate, free)
  │  Selects one of the 4 open tunnel connections to cloudflared
  │
  │  [Requests flows down the already-open outbound tunnel]
  ▼
cloudflared container (172.18.4.11) on Pi
  │  Receives proxied HTTP request from Cloudflare
  │  Forwards to: http://172.18.4.4:80 (Nginx)
  │
  │  [Docker bridge network, plain TCP]
  ▼
Nginx reverse proxy (172.18.4.4:80)
  │  Routes to: http://172.18.4.2:8123 (HA)
  ▼
Home Assistant → response tunnels back the same path
→ Mobile App receives HTTPS response with Cloudflare TLS cert
```

The Pi's public IP address is **never in DNS**. Cloudflare's IP is in DNS. Even if someone port-scans the Pi's public IP they will find no open ports.

#### New container at `172.18.4.11`

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate run
  environment:
    - TUNNEL_TOKEN=${CF_TUNNEL_TOKEN}    # from Cloudflare Zero Trust dashboard
  networks:
    hems:
      ipv4_address: 172.18.4.11
  restart: always
```

`CF_TUNNEL_TOKEN` is set in Balena Cloud environment variables. No volumes, no privileged mode, no special capabilities needed. This is intentionally the simplest container of the three access options — cloudflared only makes outbound HTTPS connections.

#### Cloudflare dashboard setup (one time)

1. Create a free Cloudflare account at `cloudflare.com`
2. Add your domain to Cloudflare (or register one — ~£10/year via Cloudflare Registrar)
3. Go to **Zero Trust → Networks → Tunnels → Create a tunnel**
4. Give the tunnel a name (e.g. `hems-pi`)
5. Cloudflare generates a `TUNNEL_TOKEN` — copy it into Balena Cloud as `CF_TUNNEL_TOKEN`
6. Configure the **Public Hostname** (ingress rule):
   - Subdomain: `hems`
   - Domain: `yourdomain.com`
   - Type: HTTP
   - URL: `172.18.4.4:80`
7. Save — Cloudflare automatically creates the DNS CNAME record

#### Nginx — no changes required

Because Cloudflare terminates TLS at the edge, your Nginx continues to serve plain HTTP on port 80. The existing `http.conf` requires no modifications. The connection between cloudflared and Nginx is internal Docker traffic — encrypted on the public leg by Cloudflare's tunnel, plain HTTP on the private leg.

Cloudflare adds the following headers to every forwarded request:
```
CF-Connecting-IP:  <mobile-phone-real-IP>
CF-Ray:            <request-trace-id>
X-Forwarded-For:   <mobile-phone-real-IP>
X-Forwarded-Proto: https
```

HA's trusted proxy config in `configuration.yaml` should include Cloudflare's IP ranges to correctly identify the real client IP:
```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 172.0.0.0/8          # Docker bridge range (Nginx, cloudflared)
    - 127.0.0.1
    - 103.21.244.0/22      # Cloudflare IP ranges
    - 103.22.200.0/22
    - 103.31.4.0/22
    - 104.16.0.0/13
    - 104.24.0.0/14
    - 108.162.192.0/18
    - 131.0.72.0/22
    - 141.101.64.0/18
    - 162.158.0.0/15
    - 172.64.0.0/13
    - 173.245.48.0/20
    - 188.114.96.0/20
    - 190.93.240.0/20
    - 197.234.240.0/22
    - 198.41.128.0/17
```

#### WebSocket support

HA's real-time UI uses WebSockets. Cloudflare supports WebSocket proxying on all plans including free. In the Cloudflare dashboard under the tunnel's ingress rule, ensure **HTTP/2** is enabled (it is by default). No special configuration is needed — Cloudflare will upgrade HTTP connections to WebSocket automatically when the client sends an `Upgrade: websocket` header.

The existing Nginx headers handle this:
```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
```

#### Cloudflare Access — second authentication layer (recommended)

Since `https://hems.yourdomain.com` is a public URL, anyone can attempt to reach the HA login page. HA's own authentication (username/password) protects the application, but Cloudflare Access adds a **pre-authentication layer at the CDN** before a request even reaches the Pi:

1. In Cloudflare Zero Trust → Access → Applications → Add an application
2. Choose: **Self-hosted**
3. Application domain: `hems.yourdomain.com`
4. Create a policy: **Allow** → Email → `your.email@example.com`
5. Now any request to `hems.yourdomain.com` must first authenticate via Cloudflare's identity page
6. Only after Cloudflare authenticates the user does the request flow to cloudflared → Nginx → HA

This stops bots, credential stuffing attacks, and random internet scanners from ever reaching HA's login page. Free tier of Cloudflare Access supports up to 50 users.

#### HA companion app configuration

In the HA app:
- **Internal URL:** `http://192.168.1.50:80` (Pi's LAN IP via Nginx)
- **External URL:** `https://hems.yourdomain.com` (Cloudflare-fronted URL)

Unlike Tailscale, no extra app installation is needed on the phone. Any browser or the HA app can access the external URL without any VPN client.

#### Trade-offs

| Pros | Cons |
|---|---|
| No client software needed on phone | Requires a domain name (~£10/year) |
| Public HTTPS URL — shareable | All traffic routes through Cloudflare's infrastructure |
| Free TLS certificate (Cloudflare-managed) | Cloudflare sees request metadata (not content — TLS between CF and cloudflared is separate) |
| No port forwarding, Pi IP never in DNS | Cloudflare outage = external access lost |
| Free tier tunnel | More setup steps than Nabu Casa |
| Cloudflare Access adds strong pre-auth | — |
| DDoS protection from Cloudflare CDN | — |

---

### Comparison Table

| | Nabu Casa | Tailscale | Cloudflare Tunnel |
|---|---|---|---|
| **Cost** | ~£6/month | Free | Free + domain (~£10/yr) |
| **Client software on phone** | HA app only | Tailscale app required | HA app only (any browser) |
| **URL format** | `https://abc.ui.nabu.casa` | `http://100.64.x.x:80` | `https://hems.yourdomain.com` |
| **TLS** | Nabu Casa CA (automatic) | WireGuard (always on) | Cloudflare CA (automatic) |
| **Pi changes needed** | None | New container + volume | New container |
| **Traffic routing** | Via Nabu Casa servers | P2P WireGuard (or DERP) | Via Cloudflare edge |
| **Pi IP in DNS** | No | No | No |
| **Inbound port needed** | No | No | No |
| **Works on CGNAT** | Yes | Yes | Yes |
| **Latency** | Medium (US relay) | Lowest (P2P) | Low (nearest CF PoP) |
| **Access control** | HA auth only | Tailscale account membership | HA auth + Cloudflare Access |
| **Shareable to others** | With HA credentials | Must add to Tailscale account | With HA credentials + Access policy |
| **Codebase changes** | None | docker-compose.yml | docker-compose.yml |

---

### Combining Multiple Solutions

All three options can run simultaneously — they are completely independent:

```
Your phone (Tailscale installed):
  Home WiFi:  http://192.168.1.50:80        → direct LAN, fastest
  4G:         http://100.64.10.5:80         → Tailscale P2P WireGuard

Guest phone (no Tailscale):
  Any network: https://hems.yourdomain.com  → Cloudflare Tunnel + Access policy

HA Cloud relay (fallback):
  Any network: https://abc.ui.nabu.casa    → Nabu Casa (if subscription active)
```

All three paths terminate at **Nginx (`172.18.4.4:80`)**, which proxies to **Home Assistant (`172.18.4.2:8123`)**. The existing WebSocket upgrade headers in `http.conf` work identically for all three:

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

HA's `trusted_proxies` config in `configuration.yaml` must include all proxy sources to correctly attribute the real client IP in logs and access control rules.

---

*Document generated from full session analysis — 2026-02-24.*
*Maintained by the SmartCORE Ireland team, Munster Technological University.*
