#!/bin/bash
# capture_test.sh — Capture a single frame from the mock or physical camera.
# Usage:
#   ./capture_test.sh --mock       Capture from /dev/video200 (Vid_Mux_TEST synthetic stream)
#   ./capture_test.sh --real       Capture from the physical USB camera (auto-detected by hardware ID)
#   ./capture_test.sh --mock --real  Capture from both sources

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}"
DOCKER_IMAGE="vid_mux_test"

KBUILD_DIR="$(dirname "$(readlink -f /lib/modules/$(uname -r)/build/scripts)")"

COMMON_MOUNTS=(
    -v /lib/modules:/lib/modules:ro
    -v /usr/src:/usr/src:ro
    -v "${KBUILD_DIR}:${KBUILD_DIR}:ro"
    -v "${OUTPUT_DIR}:/output"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
timestamp() {
    date +"%Y_%m_%d__%H_%M_%S"
}

detect_physical_camera() {
    # Resolve the immutable hardware path for the physical camera.
    # We look for the first video-index0 entry under /dev/v4l/by-id/,
    # which is stable regardless of kernel device index reordering.
    local cam
    cam=$(ls /dev/v4l/by-id/*-video-index0 2>/dev/null | head -n1)
    if [[ -z "${cam}" ]]; then
        echo "ERROR: No physical camera found under /dev/v4l/by-id/" >&2
        exit 1
    fi
    echo "${cam}"
}

capture_frame() {
    local device="${1}"          # device path inside the container
    local camera_name="${2}"     # label used in the output filename
    local host_device="${3:-}"   # host device to pass via --device (empty = none)

    local ts
    ts="$(timestamp)"
    local output_file="/output/${ts}_${camera_name}.jpg"

    echo "📸 Capturing from ${camera_name} (${device})..."

    local device_flag=()
    if [[ -n "${host_device}" ]]; then
        device_flag=(--device="${host_device}:${device}")
    fi

    docker run --rm \
        "${COMMON_MOUNTS[@]}" \
        "${device_flag[@]}" \
        --entrypoint gst-launch-1.0 \
        "${DOCKER_IMAGE}" \
        v4l2src device="${device}" num-buffers=1 \
        ! jpegenc \
        ! filesink location="${output_file}"

    echo "✅ Saved: ${OUTPUT_DIR}/${ts}_${camera_name}.jpg"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
CAPTURE_MOCK=false
CAPTURE_REAL=false

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 [--mock] [--real]"
    echo "  --mock   Capture from synthetic camera (/dev/video200)"
    echo "  --real   Capture from physical USB camera (auto-detected)"
    exit 1
fi

for arg in "$@"; do
    case "${arg}" in
        --mock) CAPTURE_MOCK=true ;;
        --real) CAPTURE_REAL=true ;;
        *)
            echo "ERROR: Unknown option '${arg}'" >&2
            echo "Usage: $0 [--mock] [--real]" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
if "${CAPTURE_MOCK}"; then
    # The mock device is already accessible inside the running vid_mux_test
    # container (it was created by that container). We pass it as a device
    # to the temporary capture container.
    capture_frame "/dev/video200" "mock_cam" "/dev/video200"
fi

if "${CAPTURE_REAL}"; then
    PHYSICAL_CAM="$(detect_physical_camera)"
    CAM_LABEL="$(basename "${PHYSICAL_CAM}" | sed 's/-video-index0//')"
    capture_frame "/dev/video0" "${CAM_LABEL}" "${PHYSICAL_CAM}"
fi