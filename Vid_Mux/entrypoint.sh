#!/bin/bash
set -euo pipefail

echo "======================================================="
echo "⚙️  Vid_Mux: Starting Video Switcher"
echo "======================================================="

if [[ ! -e /dev/video100 ]]; then
    echo "❌ Error: Physical camera /dev/video100 not found."
    exit 1
fi
echo "✅ Physical camera /dev/video100 present."

if [[ -e /dev/video200 ]]; then
    echo "✅ Mock camera /dev/video200 present (Vid_Mux_TEST running)."
else
    echo "⚠  /dev/video200 not found — source 1 will use synthetic SMPTE pattern."
fi
echo "🎥 Starting Vid_Mux (pipeline + API in single process)..."

exec python3 /opt/vid_mux/main.py