#!/usr/bin/env bash
# tests/test_containers.sh — Verify the SCANBOX container stack is healthy.
#
# Checks performed:
#   1. Docker images vid_mux and vid_mux_test exist
#   2. Containers scanbox_dhcp, vid_mux_test, and vid_mux are running
#   3. Port 80 is listening (Flask API)
#   4. Physical camera device nodes are accessible inside the vid_mux container
#      (mock/synthetic sources are skipped — vid_mux uses videotestsrc internally)
#
# Requirements: full stack must be running (rebuild_vid_mux.sh or boot service).
# Physical camera device list is read dynamically from GET /api/v1/status.
#
# Usage:
#   ./tests/test_containers.sh
#   API_BASE=http://192.168.55.1 ./tests/test_containers.sh
#
# Exit 0 = all checks passed.  Exit 1 = one or more checks failed.

set -euo pipefail

API_BASE="${API_BASE:-http://localhost}"

PASS=0
FAIL=0

pass() { echo "  PASS: $*"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $*"; FAIL=$(( FAIL + 1 )); }

echo "========================================"
echo "Container Tests"
echo "========================================"

# ── 1. Docker images exist ────────────────────────────────────────────────────
echo ""
echo "[1] Checking Docker images..."
for IMAGE in vid_mux vid_mux_test; do
    if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
        pass "Docker image '${IMAGE}' exists"
    else
        fail "Docker image '${IMAGE}' not found — run rebuild_vid_mux.sh to build"
    fi
done

# ── 2. Containers are running ─────────────────────────────────────────────────
echo ""
echo "[2] Checking running containers..."
for CONTAINER in scanbox_dhcp vid_mux_test vid_mux; do
    if docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null | grep -q "true"; then
        pass "Container '${CONTAINER}' is running"
    else
        fail "Container '${CONTAINER}' is NOT running"
    fi
done

# ── 3. Port 80 is listening ─────────────────────────────────────────────────
echo ""
echo "[3] Checking port 80..."
if ss -tlnp 2>/dev/null | grep -q ':80'; then
    pass "Port 80 is listening"
elif curl -s --max-time 3 "${API_BASE}/api/v1/status" >/dev/null 2>&1; then
    pass "Port 80 is responding to HTTP requests"
else
    fail "Port 80 is not accessible"
fi

# ── 4. Physical camera devices visible inside vid_mux ────────────────────────
# Read the source list from the API and check each physical device.
# Mock sources (no device or /dev/video200) are skipped — vid_mux uses
# videotestsrc internally for the mock camera and no longer needs video200.
echo ""
echo "[4] Checking physical device visibility inside vid_mux container..."

STATUS_JSON="$(curl -s --max-time 5 "${API_BASE}/api/v1/status" 2>/dev/null || true)"

if [ -z "${STATUS_JSON}" ]; then
    fail "API unreachable — cannot verify device mappings"
else
    mapfile -t SOURCE_LINES < <(
        echo "${STATUS_JSON}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data.get('sources', []):
    print('{id}|{name}|{device}'.format(
        id=s['id'],
        name=s.get('name', ''),
        device=s.get('device', '')
    ))"
    )

    PHYSICAL_CHECKED=0
    for line in "${SOURCE_LINES[@]}"; do
        IFS='|' read -r SRC_ID SRC_NAME SRC_DEVICE <<< "${line}"
        if [ -n "${SRC_DEVICE}" ] && [ "${SRC_DEVICE}" != "/dev/video200" ]; then
            PHYSICAL_CHECKED=$(( PHYSICAL_CHECKED + 1 ))
            if docker exec vid_mux test -e "${SRC_DEVICE}" 2>/dev/null; then
                pass "${SRC_DEVICE} (source ${SRC_ID}: ${SRC_NAME}) accessible inside vid_mux"
            else
                fail "${SRC_DEVICE} (source ${SRC_ID}: ${SRC_NAME}) NOT found inside vid_mux"
            fi
        fi
    done

    if [ "${PHYSICAL_CHECKED}" -eq 0 ]; then
        pass "No physical device mappings to verify (all sources are synthetic)"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "PASS: ${PASS}  FAIL: ${FAIL}"
echo "========================================"

[ "${FAIL}" -eq 0 ]
