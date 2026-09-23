# SmartCORE Ireland — Home Energy Management System (HEMS)

[![MTU Research](https://img.shields.io/badge/Research-MTU-blue)](https://www.mtu.ie/)
[![Interreg NWE](https://img.shields.io/badge/Project-SmartCORE--NWE-green)](https://smartcore.nweurope.eu/)
[![Platform](https://img.shields.io/badge/Platform-BalenaOS-blueviolet)](https://www.balena.io/)

A containerised **Home Energy Management System (HEMS)** for the Irish pilot of the Interreg NWE
**SmartCORE** project (MTU with South Kerry Development Partnership). A Raspberry Pi 5 running
balenaOS is installed in each participating home or SME. It reads whatever energy equipment the
site has — solar inverter, battery system, or the ESB smart meter's pulse LED — stores every
reading locally in InfluxDB, and is managed remotely as one balenaCloud fleet.

This repository is the code that runs on the box. The energy-data analysis that turns the
exported readings into a homeowner report lives on the `influx-data-analysis` branch
(`analysis/`).

---

## What is on the box

Eight containers, defined in `docker-compose.yml`, on a private bridge network `172.18.4.0/24`:

| Service | Image | Network | Role |
|---|---|---|---|
| `homeassistant` | `ghcr.io/home-assistant/home-assistant:2026.6.3` | `.2` | Reads the site's equipment through its integrations, creates `sensor.*` entities, writes every sensor change to InfluxDB. Serves the dashboard. |
| `influxdb` | `influxdb:2.7.1` | `.3` | Time-series store: org `hems`, bucket `home_assistant`. Volume `influxdb-data`. |
| `nginx-reverse-proxy` | `arm64v8/nginx` | `.4`, host `:80` | Single entry point: `/` → Home Assistant, `/influx/` → InfluxDB UI, `/configurator/` → file editor (basic auth). |
| `hass-configurator` | `causticlab/hass-configurator-docker` | `.6` | Browser editor for the Home Assistant config volume. Not published on the host; reached only through nginx with a login. |
| `mqtt` | `eclipse-mosquitto` | `.7`, host `:1883` | Mosquitto broker. **Idle**: no site currently has an MQTT integration or MQTT device. Kept for future devices. |
| `led-status` | Ubuntu + gpiozero | `.9` | Three GPIO LEDs: Home Assistant reachable (17), internet reachable (27), `ERROR` in the HA log (22). |
| `system-manager` | Alpine + Python | host | Watchdog: stops the Wi‑Fi setup hotspot 10 min after boot, restarts HA after a release update, reboots the box after 30 min without internet, adds an iptables rule. |
| `wifi-connect` | Debian + balena wifi-connect | host | Setup hotspot "WiFi Connect" **only when the box has no network at all**; WPA2-protected by `PORTAL_PASSPHRASE`. |

Full detail of every component, and what is stored on the device but not in this repo, is in
[`docs/COMPONENTS.md`](docs/COMPONENTS.md). The diagram is [`docs/architecture.png`](docs/architecture.png)
(source: [`docs/architecture_diagram.puml`](docs/architecture_diagram.puml)).

### Where the energy data comes from

The box is equipment-agnostic. Each site's data source is set up once through the Home Assistant
UI (or HACS) and lives in the `hass-config` volume on the device — **not in this repository**.
Deployed today:

| Equipment | Home Assistant integration | Sites |
|---|---|---|
| Solar inverter with Solarman logger (Sofar G3) | `solarman` (HACS custom component), Modbus/TCP over the LAN | 1 |
| Sigenergy inverter + battery | `sigenergy` (custom component), Modbus/TCP | 1 |
| ESB smart meter pulse LED → frient **EMIZB-141** Zigbee meter interface | `zha` with a Sonoff ZBDongle‑E / EZSP USB coordinator | 2 |

Home Assistant writes every `sensor` entity change to InfluxDB. Measurements are named by unit
(`W`, `kWh`, `V`, …) with the entity as the `entity_id` tag.

---

## Deploying

Fleet: `smartcoremtu/smartcore` on balenaCloud. The full procedure — variables, building a draft
release, testing it on one pinned device, finalising, rollback, and the things that have bitten
us — is in [`DEPLOYMENT.md`](DEPLOYMENT.md). The short version:

```bash
balena login
balena push smartcoremtu/smartcore --draft     # build in the cloud; nothing deploys yet
balena device pin <test-uuid> <full-commit>     # try it on one box
balena release finalize <full-commit>           # roll out to the fleet
balena device track-fleet <test-uuid>
```

`balena push` uploads this whole directory to the builder. `.dockerignore` keeps `data/`,
`analysis/` and `docs/` out of the upload — do not remove it.

### Variables (balenaCloud, set as *service* variables)

| Variable | Service | Purpose |
|---|---|---|
| `INFLUX_TOKEN` | `homeassistant` | InfluxDB 2 write token used by the HA InfluxDB integration |
| `CONFIG_USER`, `CONFIG_PASSWORD` | `nginx-reverse-proxy` | Login for `/configurator/`. Without them the configurator is refused. |
| `PORTAL_PASSPHRASE` | `wifi-connect` | WPA2 passphrase of the setup hotspot. Without it the hotspot is **open**. |
| `PORTAL_LISTENING_PORT` | `wifi-connect` | Portal HTTP port (fleet has `8080`). |
| `PORTAL_MAX_MINUTES` | `system-manager` | Minutes the hotspot may stay up after boot (default 10, `0` = never stop). |

---

## Privacy

Devices hold identifiable household energy data. Exports go in `data/` (git-ignored and
`.dockerignore`d — never commit them) and are de-identified with the project's UHIC codes before
they leave the team, as set out in the MTU ethics application.

## Project

SmartCORE (Interreg North-West Europe, NWE0400468). Irish partners: Munster Technological
University (technical lead) and South Kerry Development Partnership (community lead).
