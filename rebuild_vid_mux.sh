#!/bin/bash
# rebuild_vid_mux.sh — Start/rebuild the full SCANBOX container stack.
#
# Boot flow (called by host/scanbox.service):
#   1. Start scanbox_dhcp (DHCP for USB NCM link) — skip if already running
#   2. Detect physical cameras under /dev/v4l/by-id/
#   3. Calculate MOCK_COUNT = max(0, 2 - PHYSICAL_COUNT)
#   4. If MOCK_COUNT > 0: start vid_mux_test (loads v4l2loopback kernel module),
#      wait for /dev/video200 to appear as confirmation the module is loaded.
#      vid_mux does NOT read from /dev/video200 — mock sources use internal
#      videotestsrc inside the GStreamer pipeline.
#   5. Build SCANBOX_SOURCES (physical cameras + MOCK_COUNT virtual mock entries)
#   6. Stop, rebuild, and relaunch vid_mux with the resulting SCANBOX_SOURCES
#
# Also used manually to pick up new cameras or rebuild after code changes.
# Run from the scanbox project root: ./rebuild_vid_mux.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_ROOT}"

# ── Flags ─────────────────────────────────────────────────────────────────────
SKIP_BUILD=false
for arg in "$@"; do
    case "${arg}" in
        --skip-build) SKIP_BUILD=true ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

# ── Helper: check if a container is running ──────────────────────────────────
container_running() {
    local name="$1"
    [[ "$(docker inspect -f '{{.State.Running}}' "${name}" 2>/dev/null)" == "true" ]]
}

# ── Helper: check if a container's healthcheck is healthy ────────────────────
container_healthy() {
    local name="$1"
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "${name}" 2>/dev/null)" == "healthy" ]]
}

# ── Helper: ensure an image exists (build if not) ────────────────────────────
ensure_image() {
    local tag="$1"
    local context="$2"
    if ! docker image inspect "${tag}" &>/dev/null; then
        echo "Building image ${tag}..."
        docker build -t "${tag}" "${context}"
    fi
}

echo "========================================"
echo "SCANBOX Stack Startup"
echo "========================================"

# ── 1. scanbox_dhcp ───────────────────────────────────────────────────────────
echo ""
echo "[1] scanbox_dhcp (DHCP server)"
if container_running scanbox_dhcp; then
    echo "  Already running — skipping."
else
    ensure_image scanbox-scanbox_dhcp scanbox_dhcp/
    docker rm -f scanbox_dhcp 2>/dev/null || true
    docker run -d --name scanbox_dhcp --network=host --cap-add=NET_ADMIN \
        --restart=always scanbox-scanbox_dhcp
    echo "  Started."
fi

# ── 2. Camera Discovery ───────────────────────────────────────────────────────
echo ""
echo "[2] Camera Discovery"
echo "========================================"
# Wait for udev to finish processing USB events before enumerating cameras.
# Critical at boot: systemd may start this service before all cameras appear.
echo "  Settling udev events..."
udevadm settle --timeout=30 2>/dev/null || true

mapfile -t PHYSICAL_CAMS < <(ls /dev/v4l/by-id/*-video-index0 2>/dev/null || true)
PHYSICAL_COUNT=${#PHYSICAL_CAMS[@]}

echo "  Physical cameras found: ${PHYSICAL_COUNT}"
for i in "${!PHYSICAL_CAMS[@]}"; do
    echo "    [${i}] ${PHYSICAL_CAMS[${i}]}"
done

# Cap at 4 physical camera slots (video100..video103)
MAX_PHYSICAL=4
if [[ "${PHYSICAL_COUNT}" -gt "${MAX_PHYSICAL}" ]]; then
    echo "WARNING: ${PHYSICAL_COUNT} cameras found, only using first ${MAX_PHYSICAL}." >&2
    PHYSICAL_COUNT=${MAX_PHYSICAL}
fi

# Calculate how many mock sources to add so the total is at least 2.
# 0 physical → 2 mocks | 1 physical → 1 mock | 2+ physical → 0 mocks
MOCK_COUNT=$(( PHYSICAL_COUNT < 2 ? 2 - PHYSICAL_COUNT : 0 ))
echo "  Mock sources needed : ${MOCK_COUNT}"
echo "========================================"

# ── 3. vid_mux_test (only if mocks are needed) ────────────────────────────────
echo ""
echo "[3] vid_mux_test (v4l2loopback kernel module)"
if [[ "${MOCK_COUNT}" -eq 0 ]]; then
    echo "  Skipping — 2+ physical cameras present, no mocks needed."
else
    if container_healthy vid_mux_test; then
        echo "  Already healthy — skipping."
    else
        ensure_image scanbox-vid_mux_test Vid_Mux_TEST/

        if [[ -f "${PROJECT_ROOT}/.env" ]]; then
            KBUILD_DIR="$(grep '^KBUILD_DIR=' "${PROJECT_ROOT}/.env" | cut -d= -f2-)"
        else
            KBUILD_DIR="$(ls -d /usr/lib/linux-kbuild-* 2>/dev/null | head -1)"
        fi

        if [[ -z "${KBUILD_DIR}" ]]; then
            echo "ERROR: Cannot determine KBUILD_DIR. Run sudo ./host/setup_host.sh first." >&2
            exit 1
        fi

        echo "  KBUILD_DIR=${KBUILD_DIR}"
        docker rm -f vid_mux_test 2>/dev/null || true
        docker run -d --name vid_mux_test --network=host --privileged \
            --restart=always \
            -v /lib/modules:/lib/modules:ro \
            -v /usr/src:/usr/src:ro \
            -v "${KBUILD_DIR}:${KBUILD_DIR}:ro" \
            scanbox-vid_mux_test
        echo "  Started — waiting for /dev/video200 (kernel module load confirmation)..."
    fi

    # Wait for /dev/video200 — proves the v4l2loopback module loaded successfully.
    # vid_mux does not read from this device; it only signals readiness.
    TIMEOUT=120
    ELAPSED=0
    while [[ ! -e /dev/video200 ]]; do
        if [[ "${ELAPSED}" -ge "${TIMEOUT}" ]]; then
            echo "ERROR: /dev/video200 did not appear after ${TIMEOUT}s." >&2
            echo "       Check: docker logs vid_mux_test" >&2
            exit 1
        fi
        sleep 2
        ELAPSED=$(( ELAPSED + 2 ))
        echo "  ...${ELAPSED}s"
    done
    echo "  /dev/video200 present — kernel module loaded."
fi

# ── 4. Build SCANBOX_SOURCES JSON ─────────────────────────────────────────────
echo ""
echo "[4] Building SCANBOX_SOURCES"
DEVICE_FLAGS=()
SOURCES_JSON="["
SLOT_BASE=100
SOURCE_ID=0

for (( i=0; i<PHYSICAL_COUNT; i++ )); do
    CAM_PATH="${PHYSICAL_CAMS[${i}]}"
    SLOT_NUM=$(( SLOT_BASE + i ))
    SLOT_DEV="/dev/video${SLOT_NUM}"
    CAM_LABEL="$(basename "${CAM_PATH}" | sed 's/-video-index0$//')"

    DEVICE_FLAGS+=("--device=${CAM_PATH}:${SLOT_DEV}")

    [[ "${SOURCE_ID}" -gt 0 ]] && SOURCES_JSON+=","
    SAFE_LABEL="${CAM_LABEL//\"/\\\"}"
    SOURCES_JSON+="{\"id\":${SOURCE_ID},\"slot\":\"${SLOT_DEV}\",\"label\":\"${SAFE_LABEL}\"}"
    SOURCE_ID=$(( SOURCE_ID + 1 ))
done

for (( m=0; m<MOCK_COUNT; m++ )); do
    [[ "${SOURCE_ID}" -gt 0 ]] && SOURCES_JSON+=","
    if [[ "${MOCK_COUNT}" -eq 1 ]]; then
        MOCK_LABEL="mock_0"
    else
        MOCK_LABEL="mock_${m}"
    fi
    # slot is null — vid_mux uses internal videotestsrc for mock sources.
    SOURCES_JSON+="{\"id\":${SOURCE_ID},\"slot\":null,\"label\":\"${MOCK_LABEL}\"}"
    SOURCE_ID=$(( SOURCE_ID + 1 ))
done

SOURCES_JSON+="]"

# Write camera env file for external scripts/tests (best-effort — may be root-owned from a prior boot run)
{
    echo "CAMERA_COUNT=${PHYSICAL_COUNT}"
    for (( i=0; i<PHYSICAL_COUNT; i++ )); do
        SLOT_NUM=$(( SLOT_BASE + i ))
        CAM_LABEL_I="$(basename "${PHYSICAL_CAMS[${i}]}" | sed 's/-video-index0$//')"
        echo "CAMERA_${i}_DEVICE=${PHYSICAL_CAMS[${i}]}"
        echo "CAMERA_${i}_SLOT=/dev/video${SLOT_NUM}"
        echo "CAMERA_${i}_LABEL=${CAM_LABEL_I}"
    done
    echo "MOCK_COUNT=${MOCK_COUNT}"
    echo "SCANBOX_SOURCES=${SOURCES_JSON}"
} > /tmp/scanbox_cameras.env 2>/dev/null || echo "  (Warning: could not write /tmp/scanbox_cameras.env — skipping)"

echo "  SCANBOX_SOURCES=${SOURCES_JSON}"

# ── 5. Stop, rebuild, and relaunch vid_mux ───────────────────────────────────
echo ""
echo "[5] vid_mux (video switcher)"
docker rm -f vid_mux 2>/dev/null || true

if [[ "${SKIP_BUILD}" == "true" ]]; then
    echo "  Skipping image build (--skip-build)."
else
    echo "  Building vid_mux image..."
    docker build -t vid_mux Vid_Mux/
fi

echo "  Launching vid_mux..."
mkdir -p snapshots

docker run -d --name vid_mux --network=host \
    --restart=on-failure \
    "${DEVICE_FLAGS[@]+"${DEVICE_FLAGS[@]}"}" \
    -e SCANBOX_SOURCES="${SOURCES_JSON}" \
    -v "${PROJECT_ROOT}/snapshots:/exports/snapshots" \
    vid_mux

echo ""
echo "========================================"
echo "SCANBOX stack running."
echo "  Sources : ${SOURCES_JSON}"

# Find the first USB network interface with an assigned IPv4 (interface name may vary)
USB_WEB_IP=""
USB_WEB_IFACE=""
for _iface in $(ls /sys/class/net/ 2>/dev/null); do
    _dev=$(realpath "/sys/class/net/${_iface}/device" 2>/dev/null) || continue
    [[ "${_dev}" == *"gadget"* ]] || continue
    _ip=$(ip -4 -o addr show "${_iface}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
    if [[ -n "${_ip}" ]]; then
        USB_WEB_IP="${_ip}"
        USB_WEB_IFACE="${_iface}"
        break
    fi
done
if [[ -z "${USB_WEB_IP}" ]]; then
    USB_WEB_IP=$(hostname -I | awk '{print $1}')
fi

printf "  %-8s → http://%s\n" "Web UI" "${USB_WEB_IP}"

# Also show other externally-accessible IPs; skip loopback, the USB iface already shown,
# and virtual interfaces (docker bridges, veth pairs, etc. live under /devices/virtual/).
for _iface in $(ls /sys/class/net/ 2>/dev/null); do
    [[ "${_iface}" == "lo" ]] && continue
    [[ "${_iface}" == "${USB_WEB_IFACE}" ]] && continue
    _real=$(realpath "/sys/class/net/${_iface}" 2>/dev/null) || continue
    [[ "${_real}" == *"/devices/virtual/"* ]] && continue
    _ip=$(ip -4 -o addr show "${_iface}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
    [[ -z "${_ip}" ]] && continue
    printf "  %-8s → http://%s\n" "${_iface}" "${_ip}"
done
echo "========================================"
