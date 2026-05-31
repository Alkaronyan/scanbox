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

# ---- 4. Verification ---------------------------------------------------------
echo "🔍 [4/4] Verifying installation..."
docker --version
echo "   ✔ Kernel headers: $(ls -d /lib/modules/${KREL}/build)"

echo "======================================================="
echo "✅ Host is ready."
echo ""
echo "NEXT STEPS:"
echo "  1. Log out and back in (or run: newgrp docker) so group changes apply."
echo "  2. Build & run the test scaffold (creates /dev/video200):"
echo "       cd test/Vid_Mux_TEST"
echo "       docker build -t vid_mux_test ."
echo "       KBUILD_DIR=\$(dirname \$(readlink -f /lib/modules/\$(uname -r)/build/scripts))"
echo "       docker run --rm --privileged --network=host \\"
echo "         -v /lib/modules:/lib/modules:ro \\"
echo "         -v /usr/src:/usr/src:ro \\"
echo "         -v \"\${KBUILD_DIR}:\${KBUILD_DIR}:ro\" \\"
echo "         vid_mux_test"
echo "======================================================="
