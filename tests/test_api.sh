#!/usr/bin/env bash
# tests/test_api.sh — Exercise the Vid_Mux REST API.
#
# Requires vid_mux container to be running and accessible at API_BASE.
# Uses curl (host) if available; otherwise falls back to a docker helper.
#
# Source list is discovered dynamically from GET /api/v1/status; no
# hardcoded source IDs. Number of sources is verified against the cameras
# detected on the host.
#
# Exit 0 = all checks passed.  Exit 1 = one or more checks failed.

set -euo pipefail

API_BASE="${API_BASE:-http://localhost:5000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOTS_DIR="${SCRIPT_DIR}/../snapshots"

PASS=0
FAIL=0

pass() { echo "  PASS: $*"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $*"; FAIL=$(( FAIL + 1 )); }

echo "========================================"
echo "API Tests  (${API_BASE})"
echo "========================================"

# ── Helper: HTTP call via curl (host) or docker (fallback) ───────────────────
# Usage: http_call <method> <path> [body]
# Sets: HTTP_STATUS, HTTP_BODY
http_call() {
    local method="$1"
    local path="$2"
    local body="${3:-}"

    local curl_args=(-s -w "\n__STATUS__%{http_code}" -X "${method}")
    if [ -n "${body}" ]; then
        curl_args+=(-H "Content-Type: application/json" -d "${body}")
    fi
    curl_args+=("${API_BASE}${path}")

    local raw
    raw="$(curl "${curl_args[@]}" 2>/dev/null)"
    HTTP_STATUS="${raw##*__STATUS__}"
    HTTP_BODY="${raw%__STATUS__*}"
    HTTP_BODY="${HTTP_BODY%$'\n'}"
}

# ── Helper: check HTTP status ────────────────────────────────────────────────
check_status() {
    local expected="$1"
    local desc="$2"
    if [ "${HTTP_STATUS}" = "${expected}" ]; then
        pass "${desc} (HTTP ${HTTP_STATUS})"
        return 0
    else
        fail "${desc} — expected HTTP ${expected}, got ${HTTP_STATUS}"
        return 1
    fi
}

# ── Helper: JSON field present ───────────────────────────────────────────────
json_has() {
    local field="$1"
    echo "${HTTP_BODY}" | grep -q "\"${field}\""
}

# ── 1. GET /api/v1/status ─────────────────────────────────────────────────────
echo ""
echo "[1] GET /api/v1/status"
http_call GET /api/v1/status
if check_status 200 "Status endpoint reachable"; then
    if json_has "active_source" && json_has "sources"; then
        pass "Response contains 'active_source' and 'sources'"
    else
        fail "Response missing expected fields — got: ${HTTP_BODY}"
    fi
fi

# ── 2. Discover sources from API ──────────────────────────────────────────────
# Parse source IDs from the status response so all remaining tests are dynamic.
echo ""
echo "[2] Discover source list from API"
http_call GET /api/v1/status
# Extract all "id":N values from the sources array (simple grep approach)
mapfile -t API_SOURCE_IDS < <(echo "${HTTP_BODY}" | grep -o '"id":[0-9]*' | grep -o '[0-9]*' || true)
API_SOURCE_COUNT=${#API_SOURCE_IDS[@]}

if [ "${API_SOURCE_COUNT}" -ge 1 ]; then
    pass "API reports ${API_SOURCE_COUNT} source(s): IDs = ${API_SOURCE_IDS[*]}"
else
    fail "Could not parse source IDs from /api/v1/status response"
fi

# ── 3. Verify source count matches host camera count ─────────────────────────
echo ""
echo "[3] Verify source count matches detected cameras on host"
HOST_PHYSICAL_COUNT=$(ls /dev/v4l/by-id/*-video-index0 2>/dev/null | wc -l)
HOST_MOCK=0
[ -e /dev/video200 ] && HOST_MOCK=1
HOST_TOTAL=$(( HOST_PHYSICAL_COUNT + HOST_MOCK ))

echo "  Host cameras: ${HOST_PHYSICAL_COUNT} physical + ${HOST_MOCK} mock = ${HOST_TOTAL} total"
echo "  API sources : ${API_SOURCE_COUNT}"

if [ "${API_SOURCE_COUNT}" -eq "${HOST_TOTAL}" ] && [ "${HOST_TOTAL}" -ge 1 ]; then
    pass "API source count (${API_SOURCE_COUNT}) matches host camera count (${HOST_TOTAL})"
elif [ "${HOST_TOTAL}" -eq 0 ]; then
    pass "No cameras on host — skipping count match (API reports ${API_SOURCE_COUNT})"
else
    fail "Source count mismatch: API=${API_SOURCE_COUNT}, host=${HOST_TOTAL}"
fi

# ── 4. POST /api/v1/source → switch to each discovered source ─────────────────
echo ""
echo "[4] Switch to each discovered source"
for src_id in "${API_SOURCE_IDS[@]}"; do
    http_call POST /api/v1/source "{\"source_id\":${src_id}}"
    check_status 200 "Switch to source ${src_id}"
done

# Switch back to first source for subsequent tests
if [ "${API_SOURCE_COUNT}" -ge 1 ]; then
    FIRST_ID="${API_SOURCE_IDS[0]}"
    http_call POST /api/v1/source "{\"source_id\":${FIRST_ID}}" >/dev/null || true
fi

# ── 5. POST /api/v1/source → invalid source_id ───────────────────────────────
echo ""
echo "[5] POST /api/v1/source {source_id: 99} (invalid)"
http_call POST /api/v1/source '{"source_id":99}'
if [ "${HTTP_STATUS}" != "200" ] || echo "${HTTP_BODY}" | grep -q '"error"'; then
    pass "Invalid source rejected (HTTP ${HTTP_STATUS})"
else
    fail "Invalid source_id 99 should have been rejected"
fi

# ── 6. POST /api/v1/snapshot ─────────────────────────────────────────────────
# Switch to first source before snapshot — it's always ready.
echo ""
echo "[6] POST /api/v1/snapshot"
if [ "${API_SOURCE_COUNT}" -ge 1 ]; then
    http_call POST /api/v1/source "{\"source_id\":${API_SOURCE_IDS[0]}}" >/dev/null || true
    sleep 0.5
fi
http_call POST /api/v1/snapshot
if check_status 200 "Snapshot endpoint returns 200"; then
    SNAP_FILE="$(echo "${HTTP_BODY}" | grep -o '"filename":"[^"]*"' | cut -d'"' -f4 || true)"
    if [ -n "${SNAP_FILE}" ] && [ -f "${SNAPSHOTS_DIR}/${SNAP_FILE}" ]; then
        pass "Snapshot file '${SNAP_FILE}' appears in snapshots/"
    else
        fail "Snapshot file not found in ${SNAPSHOTS_DIR}/ (filename='${SNAP_FILE}')"
    fi
fi

# ── 7. GET /api/v1/snapshot/last ─────────────────────────────────────────────
echo ""
echo "[7] GET /api/v1/snapshot/last"
http_call GET /api/v1/snapshot/last
if check_status 200 "Last snapshot returns HTTP 200"; then
    if curl -s -I "${API_BASE}/api/v1/snapshot/last" 2>/dev/null | grep -qi "Content-Type: image/jpeg"; then
        pass "Content-Type is image/jpeg"
    else
        fail "Content-Type is not image/jpeg"
    fi
fi

# ── 8. GET /api/v1/camera/controls ───────────────────────────────────────────
echo ""
echo "[8] GET /api/v1/camera/controls"
http_call GET /api/v1/camera/controls
if check_status 200 "Camera controls endpoint reachable"; then
    MISSING=""
    for ctrl in brightness contrast saturation sharpness gain; do
        if ! json_has "${ctrl}"; then
            MISSING="${MISSING} ${ctrl}"
        fi
    done
    if [ -z "${MISSING}" ]; then
        pass "Expected controls present (brightness, contrast, saturation, sharpness, gain)"
    else
        fail "Missing controls:${MISSING}"
    fi
fi

# ── 9. POST /api/v1/camera/control — set saturation then restore ──────────────
echo ""
echo "[9] POST /api/v1/camera/control (saturation: set + restore)"
# Get current value
http_call GET /api/v1/camera/controls
ORIGINAL_SAT="$(echo "${HTTP_BODY}" | grep -o '"saturation":[0-9]*' | cut -d: -f2 || true)"
if [ -z "${ORIGINAL_SAT}" ]; then
    fail "Could not read current saturation value"
else
    http_call POST /api/v1/camera/control '{"control":"saturation","value":100}'
    if check_status 200 "Set saturation=100"; then
        # Restore
        http_call POST /api/v1/camera/control "{\"control\":\"saturation\",\"value\":${ORIGINAL_SAT}}"
        check_status 200 "Restore saturation=${ORIGINAL_SAT}"
    fi
fi

# ── 10. GET /stream ───────────────────────────────────────────────────────────
echo ""
echo "[10] GET /stream (check MJPEG Content-Type)"
STREAM_HEADERS="$(curl -s -I --max-time 3 "${API_BASE}/stream" 2>/dev/null || true)"
if echo "${STREAM_HEADERS}" | grep -q "HTTP/1"; then
    STREAM_HTTP="$(echo "${STREAM_HEADERS}" | grep "^HTTP/" | tail -1 | awk '{print $2}')"
    if [ "${STREAM_HTTP}" = "200" ]; then
        pass "GET /stream returns HTTP 200"
    else
        fail "GET /stream returned HTTP ${STREAM_HTTP}"
    fi
    if echo "${STREAM_HEADERS}" | grep -qi "multipart/x-mixed-replace"; then
        pass "Content-Type contains multipart/x-mixed-replace"
    else
        fail "Content-Type does not contain multipart/x-mixed-replace"
    fi
else
    fail "GET /stream — no HTTP response received"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "PASS: ${PASS}  FAIL: ${FAIL}"
echo "========================================"

if [ "${FAIL}" -gt 0 ]; then
    exit 1
fi
exit 0
