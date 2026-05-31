#!/usr/bin/env bash
# tests/test_api.sh — Exercise the Vid_Mux REST API.
#
# Requires vid_mux container to be running and accessible at API_BASE.
# Uses curl (host) if available; otherwise falls back to a docker helper.
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

# ── 2. POST /api/v1/source → source_id=0 ─────────────────────────────────────
echo ""
echo "[2] POST /api/v1/source {source_id: 0}"
http_call POST /api/v1/source '{"source_id":0}'
check_status 200 "Switch to source 0"

# ── 3. POST /api/v1/source → source_id=1 ─────────────────────────────────────
echo ""
echo "[3] POST /api/v1/source {source_id: 1}"
http_call POST /api/v1/source '{"source_id":1}'
check_status 200 "Switch to source 1"

# ── 4. POST /api/v1/source → source_id=99 (invalid) ─────────────────────────
echo ""
echo "[4] POST /api/v1/source {source_id: 99} (invalid)"
http_call POST /api/v1/source '{"source_id":99}'
if [ "${HTTP_STATUS}" != "200" ] || echo "${HTTP_BODY}" | grep -q '"error"'; then
    pass "Invalid source rejected (HTTP ${HTTP_STATUS})"
else
    fail "Invalid source_id 99 should have been rejected"
fi

# ── 5. POST /api/v1/snapshot ─────────────────────────────────────────────────
# Switch back to source 0 (physical camera) before snapshot — input-selector
# needs a moment to start forwarding frames after a switch, and source 0 is
# always ready. Testing snapshot functionality, not a specific source.
echo ""
echo "[5] POST /api/v1/snapshot"
http_call POST /api/v1/source '{"source_id":0}' >/dev/null
sleep 0.5
http_call POST /api/v1/snapshot
if check_status 200 "Snapshot endpoint returns 200"; then
    SNAP_FILE="$(echo "${HTTP_BODY}" | grep -o '"filename":"[^"]*"' | cut -d'"' -f4 || true)"
    if [ -n "${SNAP_FILE}" ] && [ -f "${SNAPSHOTS_DIR}/${SNAP_FILE}" ]; then
        pass "Snapshot file '${SNAP_FILE}' appears in snapshots/"
    else
        fail "Snapshot file not found in ${SNAPSHOTS_DIR}/ (filename='${SNAP_FILE}')"
    fi
fi

# ── 6. GET /api/v1/snapshot/last ─────────────────────────────────────────────
echo ""
echo "[6] GET /api/v1/snapshot/last"
http_call GET /api/v1/snapshot/last
if check_status 200 "Last snapshot returns HTTP 200"; then
    if curl -s -I "${API_BASE}/api/v1/snapshot/last" 2>/dev/null | grep -qi "Content-Type: image/jpeg"; then
        pass "Content-Type is image/jpeg"
    else
        fail "Content-Type is not image/jpeg"
    fi
fi

# ── 7. GET /api/v1/camera/controls ───────────────────────────────────────────
echo ""
echo "[7] GET /api/v1/camera/controls"
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

# ── 8. POST /api/v1/camera/control — set saturation then restore ──────────────
echo ""
echo "[8] POST /api/v1/camera/control (saturation: set + restore)"
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

# ── 9. GET /stream ────────────────────────────────────────────────────────────
echo ""
echo "[9] GET /stream (check MJPEG Content-Type)"
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
