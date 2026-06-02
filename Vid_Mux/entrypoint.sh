#!/bin/bash
set -euo pipefail

echo "======================================================="
echo "⚙️  Vid_Mux: Starting Video Switcher"
echo "======================================================="

# Verify SCANBOX_SOURCES is set and contains at least one source.
if [[ -z "${SCANBOX_SOURCES:-}" ]]; then
    echo "❌ Error: SCANBOX_SOURCES is not set. Cannot build pipeline."
    exit 1
fi
if ! python3 -c "import json,os,sys; d=json.loads(os.environ['SCANBOX_SOURCES']); sys.exit(0 if len(d)>0 else 1)"; then
    echo "❌ Error: SCANBOX_SOURCES is empty or invalid JSON."
    exit 1
fi

echo "🎥 Starting Vid_Mux (pipeline + API in single process)..."
exec python3 /opt/vid_mux/main.py