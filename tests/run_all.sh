#!/usr/bin/env bash
# tests/run_all.sh — Master test runner for the SCANBOX project.
#
# Runs all three test suites in order and prints a summary table:
#   1. test_cameras.sh  — per-source switch + snapshot + size check
#   2. test_api.sh      — all REST API endpoints
#   3. test_containers.sh — container health, port, device mappings
#
# Requirements: vid_mux, vid_mux_test, and scanbox_dhcp containers must be
# running. Physical cameras must be connected and the full stack started via
# scripts/rebuild_vid_mux.sh or at boot via scanbox.service.
#
# Usage:
#   ./tests/run_all.sh
#   API_BASE=http://192.168.55.1:5000 ./tests/run_all.sh   # test over USB NCM
#
# Exit 0 = all suites passed.  Exit 1 = one or more suites failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SUITES=(
    test_cameras.sh
    test_api.sh
    test_containers.sh
)

declare -A RESULTS

echo ""
echo "Running SCANBOX test suites..."
echo ""

for SUITE in "${SUITES[@]}"; do
    SUITE_PATH="${SCRIPT_DIR}/${SUITE}"
    echo "════════════════════════════════════════════════════════"
    echo "  Running: ${SUITE}"
    echo "════════════════════════════════════════════════════════"
    if bash "${SUITE_PATH}"; then
        RESULTS["${SUITE}"]="PASS"
    else
        RESULTS["${SUITE}"]="FAIL"
    fi
    echo ""
done

# ── Summary table ─────────────────────────────────────────────────────────────
echo "┌─────────────────────┬────────┐"
echo "│ Suite               │ Result │"
echo "├─────────────────────┼────────┤"
for SUITE in "${SUITES[@]}"; do
    RESULT="${RESULTS[${SUITE}]}"
    printf "│ %-19s │  %-4s  │\n" "${SUITE}" "${RESULT}"
done
echo "└─────────────────────┴────────┘"
echo ""

# ── Final exit code ───────────────────────────────────────────────────────────
OVERALL=0
for SUITE in "${SUITES[@]}"; do
    if [ "${RESULTS[${SUITE}]}" != "PASS" ]; then
        OVERALL=1
    fi
done

if [ "${OVERALL}" -eq 0 ]; then
    echo "All suites PASSED."
else
    echo "One or more suites FAILED."
fi
exit "${OVERALL}"
