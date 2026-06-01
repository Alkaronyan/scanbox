#!/usr/bin/env bash
# tests/test_cameras.sh — For each source reported by the API:
#   - Verify device node inside vid_mux container (physical sources only)
#   - Switch to the source via POST /api/v1/source
#   - Capture a snapshot via POST /api/v1/snapshot
#   - Verify the snapshot file exists and is larger than 5 KB
#   - Restore source 0 at the end
#
# Source list is read dynamically from GET /api/v1/status — no hardcoded IDs.

set -euo pipefail

API_BASE="${API_BASE:-http://localhost}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOTS_DIR="${SCRIPT_DIR}/../snapshots"
MIN_SNAP_BYTES=5120   # 5 KB

PASS=0
FAIL=0

pass() { echo "  PASS: $*"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $*"; FAIL=$(( FAIL + 1 )); }

echo "========================================"
echo "Camera Tests  (${API_BASE})"
echo "========================================"

# ── Fetch source list from API ────────────────────────────────────────────────
echo ""
echo "[setup] Fetching source list from ${API_BASE}/api/v1/status..."
STATUS_JSON="$(curl -s --max-time 5 "${API_BASE}/api/v1/status" 2>/dev/null || true)"
if [ -z "${STATUS_JSON}" ]; then
    echo "  FATAL: API unreachable at ${API_BASE}" >&2
    exit 1
fi

# Parse sources into lines of "id|name|device"
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

SOURCE_COUNT=${#SOURCE_LINES[@]}
if [ "${SOURCE_COUNT}" -eq 0 ]; then
    echo "  FATAL: No sources returned by API." >&2
    exit 1
fi
echo "  Found ${SOURCE_COUNT} source(s)."

# ── Test each source ──────────────────────────────────────────────────────────
echo ""
echo "[1] Testing each source..."

for line in "${SOURCE_LINES[@]}"; do
    IFS='|' read -r SRC_ID SRC_NAME SRC_DEVICE <<< "${line}"
    echo ""
    echo "  --- Source ${SRC_ID}: ${SRC_NAME} (device=${SRC_DEVICE:-none}) ---"

    # 1a. Device node check inside vid_mux container.
    #     Mock/synthetic sources have no physical device node — skip the check.
    #     /dev/video200 is no longer mapped into vid_mux (videotestsrc is used
    #     directly inside the pipeline), so skip that too.
    if [ -n "${SRC_DEVICE}" ] && [ "${SRC_DEVICE}" != "/dev/video200" ]; then
        if docker exec vid_mux test -e "${SRC_DEVICE}" 2>/dev/null; then
            pass "Source ${SRC_ID}: ${SRC_DEVICE} exists inside vid_mux"
        else
            fail "Source ${SRC_ID}: ${SRC_DEVICE} NOT found inside vid_mux"
        fi
    else
        pass "Source ${SRC_ID}: synthetic/mock — no physical device node expected inside container"
    fi

    # 1b. Switch to this source
    SWITCH_CODE="$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST -H "Content-Type: application/json" \
        -d "{\"source_id\":${SRC_ID}}" \
        "${API_BASE}/api/v1/source" 2>/dev/null || true)"
    if [ "${SWITCH_CODE}" = "200" ]; then
        pass "Source ${SRC_ID}: switch returned HTTP 200"
    else
        fail "Source ${SRC_ID}: switch returned HTTP ${SWITCH_CODE}"
        continue
    fi

    # Brief pause for the pipeline to settle on the new source
    sleep 1

    # 1c. Request snapshot
    SNAP_RESP="$(curl -s -X POST "${API_BASE}/api/v1/snapshot" 2>/dev/null || true)"
    SNAP_STATUS="$(echo "${SNAP_RESP}" | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || true)"
    SNAP_FILE="$(echo "${SNAP_RESP}"   | grep -o '"filename":"[^"]*"' | cut -d'"' -f4 || true)"

    if [ "${SNAP_STATUS}" = "success" ] && [ -n "${SNAP_FILE}" ]; then
        pass "Source ${SRC_ID}: snapshot returned success (${SNAP_FILE})"
    else
        fail "Source ${SRC_ID}: snapshot failed — response: ${SNAP_RESP}"
        continue
    fi

    # 1d. Verify snapshot file exists on host and is larger than 5 KB
    SNAP_PATH="${SNAPSHOTS_DIR}/${SNAP_FILE}"
    if [ -f "${SNAP_PATH}" ]; then
        SNAP_SIZE="$(stat -c %s "${SNAP_PATH}" 2>/dev/null || echo 0)"
        if [ "${SNAP_SIZE}" -gt "${MIN_SNAP_BYTES}" ]; then
            pass "Source ${SRC_ID}: snapshot is ${SNAP_SIZE} bytes (> 5 KB)"
        else
            fail "Source ${SRC_ID}: snapshot is only ${SNAP_SIZE} bytes (< 5 KB) — may be a blank frame"
        fi
    else
        fail "Source ${SRC_ID}: snapshot file not found at ${SNAP_PATH}"
    fi
done

# ── Restore source 0 ──────────────────────────────────────────────────────────
echo ""
echo "[2] Restoring source 0..."
RESTORE_CODE="$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" \
    -d '{"source_id":0}' \
    "${API_BASE}/api/v1/source" 2>/dev/null || true)"
if [ "${RESTORE_CODE}" = "200" ]; then
    pass "Restored to source 0"
else
    fail "Failed to restore source 0 (HTTP ${RESTORE_CODE})"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "PASS: ${PASS}  FAIL: ${FAIL}"
echo "========================================"

[ "${FAIL}" -eq 0 ]
