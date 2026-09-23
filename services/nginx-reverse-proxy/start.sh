#!/bin/sh

if [ -n "$CONFIG_USER" ] && [ -n "$CONFIG_PASSWORD" ]; then
    htpasswd -cb /etc/nginx/.passwd "$CONFIG_USER" "$CONFIG_PASSWORD"
else
    echo "WARNING: CONFIG_USER / CONFIG_PASSWORD are not set - /configurator/ will be refused until they are."
    rm -f /etc/nginx/.passwd
fi

nginx

sleep infinity