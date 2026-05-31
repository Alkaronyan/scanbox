#!/bin/bash
set -euo pipefail

# Wait for the usb0 interface to appear (created by scanbox-gadget.service on
# the host before Docker starts, but we wait defensively in case of race).
echo "[scanbox_dhcp] Waiting for usb0 interface..."
until ip link show usb0 >/dev/null 2>&1; do
    sleep 1
done
echo "[scanbox_dhcp] usb0 is up. Starting dnsmasq..."

exec dnsmasq --no-daemon --log-queries --log-facility=- --conf-file=/etc/dnsmasq.d/scanbox.conf
