#!/bin/bash
set -euo pipefail

echo "======================================================="
echo "⚙️  Vid_Mux: Starting Video Switcher"
echo "======================================================="

for dev in /dev/video100 /dev/video200; do
    if [[ ! -e "${dev}" ]]; then
        echo "❌ Error: Required device ${dev} not found."
        exit 1
    fi
done

echo "✅ Devices verified: /dev/video100 and /dev/video200 present."
echo "🎥 Starting Vid_Mux (pipeline + API in single process)..."

exec python3 /opt/vid_mux/main.py