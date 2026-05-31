#!/bin/bash
# rebuild_vid_mux.sh — Stop, rebuild, and relaunch the Vid_Mux container.
# Run from the scanbox project root: ./rebuild_vid_mux.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

PHYSICAL_CAM="$(ls /dev/v4l/by-id/*-video-index0 2>/dev/null | head -n1)"
if [[ -z "${PHYSICAL_CAM}" ]]; then
  echo "❌ No physical camera found under /dev/v4l/by-id/" >&2
  exit 1
fi

echo "🛑 Stopping and removing vid_mux..."
docker rm -f vid_mux 2>/dev/null || true

echo "🔨 Building vid_mux image..."
docker build -t vid_mux app/Vid_Mux/

echo "🚀 Launching vid_mux..."
mkdir -p app/snapshots
docker run -d --name vid_mux --network=host \
  --device="${PHYSICAL_CAM}:/dev/video100" \
  --device=/dev/video200:/dev/video200 \
  -v "${SCRIPT_DIR}/app/snapshots:/exports/snapshots" \
  vid_mux

echo ""
echo "✅ vid_mux running."
echo "   Web UI  → http://$(hostname -I | awk '{print $1}'):5000"
echo "   Stream  → tcp://$(hostname -I | awk '{print $1}'):9000"
