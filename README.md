# SmartCORE Ireland: Community Energy Management System (HEMS)

[![MTU Research](https://img.shields.io/badge/Research-MTU-blue)](https://www.mtu.ie/)
[![Interreg NWE](https://img.shields.io/badge/Project-SmartCORE--NWE-green)](https://smartcore.nweurope.eu/)
[![Platform](https://img.shields.io/badge/Platform-BalenaOS-blueviolet)](https://www.balena.io/)

A robust, containerized **Home Energy Management System (HEMS)** developed for the **SmartCORE project in Ireland**. This system leverages Raspberry Pi and Zigbee to provide real-time energy monitoring and decentralized optimization for Smart Energy Communities (SECs).

---

## 🚀 Overview
The SmartCORE project addresses the mismatch between renewable energy (RES) production and consumption. This gateway acts as the **Local Intelligence Layer**, enabling:
* **Integral Energy Management:** Simple monitoring service.
* **Self-Healing Infrastructure:** Automated recovery and remote management via BalenaCloud.

---

## 🏗 Microservices Architecture
The system uses a decoupled multi-container design for resilience, orchestrated via Docker Compose.

| Service | Technology | Description |
| :--- | :--- | :--- |
| **`system-manager`** | Python / Alpine | **The Watchdog.** Manages internet health, Balena API sync, and iptables. |
| **`homeassistant`** | HA Core | **The Hub.** UI and logic engine. Bridges Zigbee data to InfluxDB. |
| **`influxdb`** | InfluxDB 2.7 | **The Memory.** High-performance time-series storage for energy metrics ($P, V, I$). |
| **`mqtt`** | Mosquitto | **The Nervous System.** Low-latency Zigbee message ingestion. |
| **`nginx-proxy`** | Nginx | **The Gateway.** Secure routing with dynamic path-rewriting for InfluxDB. |
| **`led-status`** | Python / Ubuntu | **Physical Diagnostics.** GPIO indicators for WAN and service health. |
| **`wifi-connect`** | Balena | **Onboarding.** Captive portal for easy Wi-Fi configuration in the field. |

---

## 🛠 Key Features
* **Zero-Touch Maintenance:** Automated reboots and service recovery via a custom Python watchdog.
* **Granular Monitoring:** Periodic energy ingestion from Zigbee smart meters/DIN-rail monitors.
* **Privacy-First:** Local data processing with secure, encrypted communication to the InfluxDB engine.
* **Scalable Deployment:** Built for **BalenaCloud**, allowing one-click deployment across Irish pilot sites.

---

## 📋 Installation & Deployment

### 1. Prerequisites
* **Hardware:** Raspberry Pi 4/5 + Zigbee USB Coordinator (e.g., Sonoff ZBDongle-E).
* **Environment:** A BalenaCloud Fleet configured for `arm64`.

### 2. Environment Variables
Configure the following in your Balena dashboard:
* `INFLUX_TOKEN`: API Token for InfluxDB 2.x.
* `CONFIG_USER` / `CONFIG_PASSWORD`: Credentials for Nginx basic auth.
* `BALENA_APP_NAME`: Used by the watchdog to identify services.

### 3. Deploy
```bash
# Push to your Balena fleet
balena push <Your-Fleet-Name>
```

## 🛠 Research & Development
This project is part of the SmartCORE Interreg NWE initiative, led in Ireland by Munster Technological University (MTU).