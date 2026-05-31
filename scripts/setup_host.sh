#!/usr/bin/env bash
#
# setup_host.sh - SCANBOX host bootstrap for a FRESH Raspberry Pi.
# =============================================================================
# ARCHITECTURE PHILOSOPHY
#   Everything that CAN run inside a container DOES (both the production
#   application and the test scaffold). This script installs ONLY the few items
#   that MUST live on the host OS/kernel and cannot be containerized:
#
#     1. Docker Engine (+ compose plugin) -> the container runtime/platform.
#     2. Kernel headers (linux-headers-*) -> REQUIRED so the test container can
#        compile the v4l2loopback module against the RUNNING host kernel and
#        insert it into the shared kernel. The headers must physically exist on
#        the host because the container mounts /usr/src and /lib/modules (ro).
#     3. git -> to clone/update this repository on the host.
#
#   Anything else (GStreamer, Python, v4l-utils, DKMS, build toolchain,
#   v4l2loopback sources) is provided INSIDE the containers and must NOT be
#   installed here.
#
# USAGE:   sudo ./setup_host.sh
# TARGET:  Raspberry Pi OS / Debian (aarch64). Tested on Debian 13 "trixie".
# =============================================================================
set -euo pipefail

# ---- Guard: must run as root (for apt and Docker installation) ---------------
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ This script must be run as root. Try: sudo ./setup_host.sh"
    exit 1
fi

# The unprivileged user that should be granted Docker access.
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "${USER:-root}")}"
KREL="$(uname -r)"

echo "======================================================="
echo "🚀 SCANBOX host bootstrap"
echo "   User to grant docker access : ${TARGET_USER}"
echo "   Running kernel              : ${KREL}"
echo "======================================================="

# ---- 1. Base utilities -------------------------------------------------------
echo "📦 [1/4] Installing base utilities (git, curl, ca-certificates)..."
apt-get update
apt-get install -y ca-certificates curl git

# ---- 2. Kernel headers (REQUIRED for in-container v4l2loopback build) --------
echo "🧩 [2/4] Installing kernel headers for ${KREL}..."
# On modern Raspberry Pi OS the meta-packages pull the correct per-board headers.
# We try the known candidates and verify the build symlink afterwards.
HEADER_CANDIDATES=(
    "linux-headers-rpi-v8"
    "linux-headers-rpi-2712"
    "linux-headers-rpi"
    "raspberrypi-kernel-headers"
    "linux-headers-${KREL}"
)
for pkg in "${HEADER_CANDIDATES[@]}"; do
    if apt-get install -y "${pkg}" 2>/dev/null; then
        echo "   ✔ Installed ${pkg}"
    fi
done

if [ ! -d "/lib/modules/${KREL}/build" ]; then
    echo "❌ Kernel headers for ${KREL} not found at /lib/modules/${KREL}/build."
    echo "   v4l2loopback cannot be compiled inside the container without them."
    echo "   Install the matching headers package manually and re-run."
    exit 1
fi
echo "   ✔ Headers reachable at /lib/modules/${KREL}/build"

# ---- 3. Docker Engine --------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    echo "🐳 [3/4] Docker already installed: $(docker --version)"
else
    echo "🐳 [3/4] Installing Docker Engine via the official convenience script..."
    # The convenience script auto-detects the distro (incl. Raspberry Pi OS).
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh
    rm -f /tmp/get-docker.sh
fi

# Enable and start the Docker service on boot.
systemctl enable --now docker || true

# Allow the target user to run docker without sudo (effective after re-login).
if [ "${TARGET_USER}" != "root" ]; then
    usermod -aG docker "${TARGET_USER}"
    echo "   ✔ Added '${TARGET_USER}' to the 'docker' group (re-login required)."
fi

# ---- 4. Docker Compose plugin ------------------------------------------------
echo "🐳 [4/5] Checking Docker Compose plugin..."
if docker compose version >/dev/null 2>&1; then
    echo "   ✔ Docker Compose available: $(docker compose version)"
else
    echo "   ⚠ Docker Compose plugin not found. Installing..."
    apt-get install -y docker-compose-plugin
    echo "   ✔ Docker Compose installed: $(docker compose version)"
fi

# ---- 5. USB NCM Gadget (Phase 3) ---------------------------------------------
# The USB gadget must run on the host because it requires direct access to the
# kernel's configfs filesystem (/sys/kernel/config). This cannot be done from
# inside a container without making it fully privileged and sharing the host
# kernel namespace — which would defeat the containerization model entirely.
#
# See docs/ARCH_USB_GADGET.md for the full rationale.
echo "🔌 [5/5] Configuring USB NCM gadget (Phase 3)..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REBOOT_REQUIRED=""

# 5a. dtoverlay=dwc2 in /boot/firmware/config.txt
if grep -qF "dtoverlay=dwc2,dr_mode=peripheral" /boot/firmware/config.txt; then
    echo "   ✔ dtoverlay=dwc2,dr_mode=peripheral already in config.txt"
else
    echo "" >> /boot/firmware/config.txt
    echo "# Scanbox Phase 3: USB NCM gadget — dwc2 in peripheral (device) mode" >> /boot/firmware/config.txt
    echo "dtoverlay=dwc2,dr_mode=peripheral" >> /boot/firmware/config.txt
    echo "   ✔ Added dtoverlay=dwc2,dr_mode=peripheral to config.txt"
    REBOOT_REQUIRED=1
fi

# 5b. Kernel modules loaded at boot
if [ -f /etc/modules-load.d/scanbox-gadget.conf ]; then
    echo "   ✔ /etc/modules-load.d/scanbox-gadget.conf already exists"
else
    cat > /etc/modules-load.d/scanbox-gadget.conf << 'EOF'
# Scanbox Phase 3: USB NCM gadget modules
libcomposite
usb_f_ncm
EOF
    echo "   ✔ Created /etc/modules-load.d/scanbox-gadget.conf"
fi

# 5c. Install gadget setup script
install -m 0755 "${SCRIPT_DIR}/setup_usb_gadget.sh" /usr/local/sbin/setup_usb_gadget.sh
echo "   ✔ Installed /usr/local/sbin/setup_usb_gadget.sh"

# 5d. Install and enable systemd services
install -m 0644 "${SCRIPT_DIR}/../systemd/scanbox-gadget.service" /etc/systemd/system/scanbox-gadget.service
install -m 0644 "${SCRIPT_DIR}/../systemd/scanbox-stack.service"  /etc/systemd/system/scanbox-stack.service
systemctl daemon-reload
systemctl enable scanbox-gadget.service
systemctl enable scanbox-stack.service
echo "   ✔ scanbox-gadget.service installed and enabled"
echo "   ✔ scanbox-stack.service  installed and enabled"

# 5e. Generate .env with KBUILD_DIR for docker-compose
# docker-compose needs this to mount the kernel build scripts into vid_mux_test.
# Must be regenerated after every kernel update.
KBUILD_DIR_VAL="$(dirname "$(readlink -f /lib/modules/${KREL}/build/scripts)")"
echo "KBUILD_DIR=${KBUILD_DIR_VAL}" > "${PROJECT_ROOT}/.env"
echo "   ✔ Generated .env: KBUILD_DIR=${KBUILD_DIR_VAL}"

# ---- Verification ------------------------------------------------------------
echo "🔍 Verifying installation..."
docker --version
docker compose version
echo "   ✔ Kernel headers: $(ls -d /lib/modules/${KREL}/build)"
echo "   ✔ Gadget script : $(ls /usr/local/sbin/setup_usb_gadget.sh)"
echo "   ✔ Systemd unit  : $(systemctl is-enabled scanbox-gadget.service)"

echo "======================================================="
echo "✅ Host is ready."
echo ""

if [ -n "${REBOOT_REQUIRED}" ]; then
    echo "⚠  REBOOT REQUIRED — config.txt was modified (dtoverlay=dwc2)."
    echo "   After reboot, run this script again to finish setup,"
    echo "   then start the stack with:"
else
    echo "   Start the full stack with:"
fi
echo ""
echo "     cd ${PROJECT_ROOT}"
echo "     docker compose up -d --build"
echo ""
echo "   Access the web UI (once a device connects via USB):"
echo "     http://192.168.55.1:5000"
echo "======================================================="
