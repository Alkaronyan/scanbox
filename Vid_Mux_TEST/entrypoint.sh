#!/bin/bash
# Prevent the script from continuing if any command fails
set -e

# Canonical upstream repository and pinned ref for the loopback kernel module.
# Pinning avoids surprise breakage against the host kernel ABI.
V4L2LOOPBACK_REPO="https://github.com/umlaeute/v4l2loopback.git"
V4L2LOOPBACK_REF="v0.15.3"

echo "======================================================="
echo "⚙️  Vid_Mux_TEST: Initializing Mocking Scaffold Engine"
echo "======================================================="

KREL="$(uname -r)"

# The host modules tree must be mapped in. Building an out-of-tree module needs
# THREE host paths mounted read-only, because of the kernel header symlink chain:
#   /lib/modules/<rel>/build         -> /usr/src/linux-headers-<rel>
#   /usr/src/linux-headers-*/scripts -> /usr/lib/linux-kbuild-<ver>/scripts
# So the container must be started with all three:
#   -v /lib/modules:/lib/modules:ro
#   -v /usr/src:/usr/src:ro
#   -v <kbuild-dir>:<kbuild-dir>:ro   (e.g. /usr/lib/linux-kbuild-<ver>)
if [ ! -d "/lib/modules/${KREL}" ]; then
    echo "❌ Error: Host kernel modules are not mounted at /lib/modules/${KREL}"
    echo "👉 Run the container mapping: -v /lib/modules:/lib/modules:ro"
    exit 1
fi

# Resolve the build symlink to make sure the actual headers are reachable.
if [ ! -d "/lib/modules/${KREL}/build" ]; then
    echo "❌ Error: Kernel headers (build dir) not reachable for ${KREL}."
    echo "👉 Also mount the headers source: -v /usr/src:/usr/src:ro"
    echo "👉 And ensure the host has the headers package installed."
    exit 1
fi

# The kbuild 'scripts' directory lives OUTSIDE /usr/src (in /usr/lib/linux-kbuild-*)
# and is reached via a symlink. If it dangles, the kbuild mount is missing.
if [ ! -e "/lib/modules/${KREL}/build/scripts/Kbuild.include" ]; then
    echo "❌ Error: Kernel build scripts not reachable (dangling 'scripts' symlink)."
    echo "👉 The scripts dir lives in /usr/lib/linux-kbuild-*; mount it read-only, e.g.:"
    echo "   KBUILD_DIR=\$(dirname \$(readlink -f /lib/modules/\$(uname -r)/build/scripts))"
    echo "   docker run ... -v \"\${KBUILD_DIR}:\${KBUILD_DIR}:ro\" ..."
    exit 1
fi

# Clone, compile, and install v4l2loopback dynamically into the container space
if [ ! -d "/tmp/v4l2loopback" ]; then
    echo "📥 Cloning v4l2loopback (${V4L2LOOPBACK_REF}) from ${V4L2LOOPBACK_REPO}..."
    git clone --depth 1 --branch "${V4L2LOOPBACK_REF}" "${V4L2LOOPBACK_REPO}" /tmp/v4l2loopback
fi

cd /tmp/v4l2loopback

# Only build if the module artifact is not already present (idempotent restarts)
if [ ! -f "v4l2loopback.ko" ]; then
    echo "🔨 Compiling v4l2loopback kernel module against host kernel ${KREL}..."
    make KERNELRELEASE="${KREL}"
fi

# Insert only if not already present in the shared kernel (idempotent)
if ! lsmod | grep -q "^v4l2loopback"; then
    echo "📦 Inserting module into the Linux kernel at high index 200..."
    # video_nr=200 forces the creation of /dev/video200 exclusively
    # card_label brands the device for easy visual identification
    # exclusive_caps=1 tricks GStreamer/Chrome into seeing it as a pure capture card
    insmod v4l2loopback.ko video_nr=200 card_label="Scanbox_Virtual_Cam" exclusive_caps=0
else
    echo "ℹ️  v4l2loopback already loaded in the shared kernel; skipping insmod."
fi

echo "✅ Kernel module injected successfully into the Host OS."
echo "   /dev/video200 is now available as a V4L2 loopback device."
echo "   No stream is written to it — vid_mux uses internal videotestsrc for mock sources."

exec sleep infinity
