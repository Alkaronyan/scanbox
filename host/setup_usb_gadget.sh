#!/usr/bin/env bash
# setup_usb_gadget.sh — Configure the USB NCM network gadget on the Pi 4.
#
# MUST RUN AS ROOT. Invoked automatically by scanbox-gadget.service at boot.
# Safe to run manually at any time — fully idempotent.
#
# What this script does (and why it must be on the host, not in a container):
#   1. Loads kernel modules: libcomposite, usb_f_ncm
#   2. Creates a USB gadget definition in configfs (/sys/kernel/config)
#   3. Binds the gadget to the dwc2 UDC — Pi 4 USB-C port appears as an
#      NCM ethernet adapter to the connected Windows PC
#   4. Assigns the static IP 192.168.199.1/30 to the resulting usb0 interface
#
# configfs is a kernel-space filesystem. Manipulating it requires root and
# direct access to /sys — it cannot be done from inside a container without
# making the container fully privileged AND sharing the host PID/kernel
# namespace, which would defeat the security model entirely.
#
# Network: 192.168.199.0/30
#   Pi (device side) : 192.168.199.1  — set here
#   Windows (host)   : 192.168.199.2  — assigned by scanbox_dhcp container
#
# =============================================================================
set -euo pipefail

GADGET_DIR="/sys/kernel/config/usb_gadget/scanbox"
UDC="fe980000.usb"
USB_IF="usb0"
STATIC_IP="192.168.199.1/30"

# ── Idempotency guard ─────────────────────────────────────────────────────────
# If gadget exists and is already bound to the UDC, there is nothing to do.
if [ -d "${GADGET_DIR}" ]; then
    BOUND_UDC="$(cat "${GADGET_DIR}/UDC" 2>/dev/null || true)"
    if [ "${BOUND_UDC}" = "${UDC}" ]; then
        echo "[gadget] Scanbox NCM gadget already active on ${UDC}. Nothing to do."
        exit 0
    fi
    echo "[gadget] Gadget directory exists but is not bound — reconfiguring."
fi

# ── 1. Load kernel modules ────────────────────────────────────────────────────
echo "[gadget] Loading kernel modules..."
modprobe libcomposite
modprobe usb_f_ncm

# ── 2. Mount configfs if needed ───────────────────────────────────────────────
if ! mountpoint -q /sys/kernel/config; then
    echo "[gadget] Mounting configfs..."
    mount -t configfs none /sys/kernel/config
fi

# ── 3. Create gadget ──────────────────────────────────────────────────────────
echo "[gadget] Creating gadget definition..."
mkdir -p "${GADGET_DIR}"

# USB device descriptor
echo 0x1d6b > "${GADGET_DIR}/idVendor"   # Linux Foundation
echo 0x0106 > "${GADGET_DIR}/idProduct"  # NCM Network Gadget
echo 0x0100 > "${GADGET_DIR}/bcdDevice"  # v1.0.0
echo 0x0200 > "${GADGET_DIR}/bcdUSB"     # USB 2.0

# Human-readable strings (English)
mkdir -p "${GADGET_DIR}/strings/0x409"
echo "deadbeef1234"          > "${GADGET_DIR}/strings/0x409/serialnumber"
echo "Scanbox"               > "${GADGET_DIR}/strings/0x409/manufacturer"
echo "Scanbox NCM Network"   > "${GADGET_DIR}/strings/0x409/product"

# ── 4. NCM function ───────────────────────────────────────────────────────────
mkdir -p "${GADGET_DIR}/functions/ncm.usb0"
# Locally administered unicast MACs — safe for private use, never conflict with
# real hardware. host_addr = Windows side, dev_addr = Pi's usb0 side.
echo "02:00:00:00:00:02" > "${GADGET_DIR}/functions/ncm.usb0/host_addr"
echo "02:00:00:00:00:01" > "${GADGET_DIR}/functions/ncm.usb0/dev_addr"

# ── 5. USB configuration ──────────────────────────────────────────────────────
mkdir -p "${GADGET_DIR}/configs/c.1/strings/0x409"
echo "CDC NCM"  > "${GADGET_DIR}/configs/c.1/strings/0x409/configuration"
echo 250        > "${GADGET_DIR}/configs/c.1/MaxPower"  # 250 x 2mA = 500mA

# Link NCM function into configuration
ln -sf "${GADGET_DIR}/functions/ncm.usb0" "${GADGET_DIR}/configs/c.1/"

# ── 6. Bind to UDC ───────────────────────────────────────────────────────────
echo "[gadget] Binding to UDC ${UDC}..."
echo "${UDC}" > "${GADGET_DIR}/UDC"

# ── 7. Configure usb0 IP ─────────────────────────────────────────────────────
echo "[gadget] Waiting for ${USB_IF} interface..."
for i in $(seq 1 20); do
    if ip link show "${USB_IF}" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

if ! ip link show "${USB_IF}" >/dev/null 2>&1; then
    echo "[gadget] WARNING: ${USB_IF} did not appear after 10s. IP not configured."
    exit 1
fi

ip link set "${USB_IF}" up
# 'ip addr replace' is idempotent — safe to run even if IP is already set
ip addr replace "${STATIC_IP}" dev "${USB_IF}"

echo "[gadget] Done. usb0 is UP at ${STATIC_IP%/*}."
