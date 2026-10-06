#!/bin/sh

# configuration scripts. Run in background but should exit
/tmp/scripts/iptables.sh &

# Run the watchdog script. Shouldn't exit
/tmp/scripts/watchdog.py