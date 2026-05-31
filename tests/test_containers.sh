#!/usr/bin/env bash
# tests/test_containers.sh — Verify Docker images, running containers, and
# device accessibility inside containers.
#
# Exit 0 = all checks passed.  Exit 1 = one or more checks failed.

set -euo pipefail

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
if docker image inspect vid_mux >/dev/null 2>&1; then
    pass "Docker image 'vid_mux' exists"
else
    fail "Docker image 'vid_mux' not found — run: docker compose build"
fi

if docker image inspect vid_mux_test >/dev/null 2>&1; then
    pass "Docker image 'vid_mux_test' exists"
else
    fail "Docker image 'vid_mux_test' not found — run: docker build -t vid_mux_test Vid_Mux_TEST/"
fi

# ── 2. Containers are running ─────────────────────────────────────────────────
echo ""
echo "[2] Checking running containers..."
if docker inspect -f '{{.State.Running}}' vid_mux 2>/dev/null | grep -q "true"; then
    pass "Container 'vid_mux' is running"
else
    fail "Container 'vid_mux' is NOT running"
fi

if docker inspect -f '{{.State.Running}}' vid_mux_test 2>/dev/null | grep -q "true"; then
    pass "Container 'vid_mux_test' is running"
else
    fail "Container 'vid_mux_test' is NOT running"
fi

# ── 3. Port 5000 is listening ─────────────────────────────────────────────────
echo ""
echo "[3] Checking port 5000..."
if ss -tlnp 2>/dev/null | grep -q ':5000'; then
    pass "Port 5000 is listening"
elif curl -s --max-time 3 http://localhost:5000/api/v1/status >/dev/null 2>&1; then
    pass "Port 5000 is responding to HTTP requests"
else
    fail "Port 5000 is not accessible"
fi

# ── 4. Device accessibility inside vid_mux ────────────────────────────────────
echo ""
echo "[4] Checking device visibility inside vid_mux container..."
if docker exec vid_mux test -e /dev/video100 2>/dev/null; then
    pass "/dev/video100 accessible inside vid_mux"
else
    fail "/dev/video100 NOT found inside vid_mux — device mapping may be missing"
fi

if [ -e /dev/video200 ]; then
    # Host has video200 (Vid_Mux_TEST running) — must be mapped inside vid_mux too.
    if docker exec vid_mux test -e /dev/video200 2>/dev/null; then
        pass "/dev/video200 accessible inside vid_mux"
    else
        fail "/dev/video200 exists on host but NOT mapped inside vid_mux — restart: docker compose up -d"
    fi
else
    pass "/dev/video200 absent on host (production mode, no Vid_Mux_TEST) — OK"
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
