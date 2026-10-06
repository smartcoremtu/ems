#!/usr/bin/env bash

export DBUS_SYSTEM_BUS_ADDRESS=unix:path=/host/run/dbus/system_bus_socket

# The captive portal is only for a box that has no network at all. A box on Ethernet has no
# WiFi association, so testing WiFi alone (iwgetid) starts a setup hotspot that never goes away.
# Treat either a default route (Ethernet or WiFi) or an associated WiFi network as "connected".
has_network() {
    ip route show default 2>/dev/null | grep -q . || iwgetid -r >/dev/null 2>&1
}

# DHCP can take a while after boot; give it a minute before deciding there is no network.
for _ in $(seq 1 12); do
    has_network && break
    sleep 5
done

# wifi-connect's access point is a NetworkManager profile on the host, so it outlives the
# container if wifi-connect was killed rather than stopped. Remove a leftover one.
remove_stale_portal() {
    local ssid="${PORTAL_SSID:-WiFi Connect}"
    if [ "$(nmcli -g 802-11-wireless.mode connection show "$ssid" 2>/dev/null)" = "ap" ]; then
        printf 'Removing leftover access point "%s"\n' "$ssid"
        nmcli connection delete "$ssid"
    fi
}

if has_network; then
    printf 'Network is up - skipping WiFi Connect\n'
    remove_stale_portal
else
    if [ -z "${PORTAL_PASSPHRASE}" ]; then
        printf 'WARNING: PORTAL_PASSPHRASE is not set - the setup hotspot will be OPEN. Set it as a fleet variable (8+ characters).\n'
    fi
    printf 'No network - starting WiFi Connect\n'
    # Run in the background and forward stop signals, so wifi-connect can take its access point
    # down when the service is stopped (bash as PID 1 does not pass SIGTERM to a foreground child).
    ./wifi-connect &
    portal=$!
    trap 'kill -TERM "$portal" 2>/dev/null; wait "$portal"; exit 0' TERM INT
    wait "$portal"
fi

trap 'exit 0' TERM INT
sleep infinity &
wait $!
