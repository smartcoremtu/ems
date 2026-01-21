# Deployment & Testing Guide

This guide covers deploying and testing the SmartCORE HEMS on a Raspberry Pi 5 with Balena Cloud.

---

## Prerequisites

- **Hardware:** Raspberry Pi 5 (or Pi 4 64-bit)
- **Optional:** Zigbee USB Coordinator (e.g., Sonoff ZBDongle-E)
- **Software:** [Balena CLI](https://docs.balena.io/reference/balena-cli/) installed locally
- **Account:** Balena Cloud subscription

---

## Step 1: Create a Balena Fleet

1. Log into [Balena Cloud](https://dashboard.balena-cloud.com/)
2. Create a new Fleet:
   - **Name:** `SmartCORE` (or your preference)
   - **Device Type:** `Raspberry Pi 5`
   - **Application Type:** `Starter` or your subscription tier

---

## Step 2: Add Your Raspberry Pi

1. Click **Add Device** in your fleet
2. Select your network configuration:
   - **Ethernet** (easiest for testing)
   - Or configure WiFi credentials
3. Download the BalenaOS image
4. Flash it to your SD card using [Balena Etcher](https://etcher.balena.io/)
5. Insert the SD card into your Pi and power on
6. Wait for the device to appear online in the dashboard

---

## Step 3: Set Environment Variables

In Balena Dashboard → Your Fleet → **Variables**, add:

| Variable | Value |
|----------|-------|
| `INFLUX_TOKEN` | Generate a secure token string |
| `CONFIG_USER` | Your desired username (e.g., `admin`) |
| `CONFIG_PASSWORD` | Your desired password |

---

## Step 4: Deploy the Code

From your local machine:

```bash
# Navigate to the project directory
cd ems

# Login to Balena (if not already)
balena login

# Push to your fleet
balena push <Your-Fleet-Name>
```

The build will take several minutes on first deploy.

---

## Step 5: Verify Deployment

Once deployed, check in Balena Dashboard:

### Services Tab
All 7 containers should show as "Running":
- homeassistant
- influxdb
- mqtt
- nginx-reverse-proxy
- system-manager
- led-status
- wifi-connect

### Logs Tab
Check for errors in each service.

---

## Step 6: Access the System

Find your device's IP address in Balena Dashboard (or use `<device-uuid>.balena-devices.com`).

| Interface | URL | Credentials |
|-----------|-----|-------------|
| **Home Assistant** | `http://<device-ip>/` | Create account on first visit |
| **InfluxDB** | `http://<device-ip>/influx/` | Basic auth (CONFIG_USER/PASSWORD) |

---

## Health Checks

From Balena Dashboard terminal (select any container):

```bash
# Test Home Assistant is responding
curl -s http://172.18.4.2:8123 | head -5

# Test InfluxDB
curl -s http://172.18.4.3:8086/health

# Test MQTT broker
curl -s http://172.18.4.7:1883 || echo "MQTT running (no HTTP response expected)"
```

### LED Status Indicators
If LEDs are connected to GPIO 17 & 27:
- **LED 1 (GPIO 17):** Home Assistant health
- **LED 2 (GPIO 27):** Internet connectivity

---

## Zigbee Setup (Optional)

If you have a Zigbee USB coordinator (e.g., Sonoff ZBDongle-E):

1. Plug it into the Pi's USB port
2. In Home Assistant, go to **Settings → Devices & Services → Add Integration**
3. Search for **Zigbee Home Automation (ZHA)** or **Zigbee2MQTT**
4. Select the USB device (usually `/dev/ttyUSB0` or `/dev/ttyACM0`)

---

## Network Architecture

Internal container network: `172.18.4.0/24`

| Service | IP Address | Port |
|---------|------------|------|
| homeassistant | 172.18.4.2 | 8123 |
| influxdb | 172.18.4.3 | 8086 |
| nginx-reverse-proxy | 172.18.4.4 | 80 |
| hass-configurator | 172.18.4.6 | 3218 |
| mqtt | 172.18.4.7 | 1883 |
| led-status | 172.18.4.9 | - |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Services not starting | Check logs in Balena Dashboard for specific errors |
| Can't access web UI | Verify nginx-reverse-proxy is running; check port 80 is exposed |
| No network between containers | Verify `hems` network exists; restart all services |
| WiFi portal not appearing | wifi-connect only runs for first 10 minutes after boot |
| Home Assistant not responding | Check homeassistant logs; may need more time on first boot |
| InfluxDB authentication failed | Verify INFLUX_TOKEN environment variable is set |

---

## Useful Commands

```bash
# Check all running containers (from Balena terminal)
balena ps

# View real-time logs for a service
balena logs <service-name> --tail

# Restart a specific service
balena restart <service-name>

# SSH into device
balena ssh <device-uuid>
```

---

## Support

For issues and feature requests, please open an issue in the repository.
