#!/usr/bin/env bash
# tests/run_all.sh — Run all SCANBOX test suites and print a summary table.
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
