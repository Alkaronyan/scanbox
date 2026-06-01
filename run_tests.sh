#!/usr/bin/env bash
# run_tests.sh — Interactive test runner for the SCANBOX pytest suite.
#
# PURPOSE
# -------
# Launches the SCANBOX test suite inside the test_runner Docker container,
# which is defined in docker-compose.yml under the "test" profile. The
# test_runner container shares the host network, the Docker socket, and the
# project bind-mount, so all five test layers can run without any host-side
# tool installation (ffmpeg, pytest, etc.).
#
# USAGE
# -----
# Without arguments — shows an interactive numbered menu:
#   ./run_tests.sh
#
# With arguments — passes them directly to pytest inside the container:
#   ./run_tests.sh -m layer1
#   ./run_tests.sh -m layer4 -v
#   ./run_tests.sh tests/layer5_behavior/test_behavior.py::test_mock_source_differs_from_physical
#   ./run_tests.sh --co -q          (list tests without running them)
#
# MENU OPTIONS
# ------------
# The menu maps each number to a pytest -m marker (except "all"):
#   1) All layers    → pytest tests/ (no marker filter)
#   2) Layer 1       → pytest -m layer1  (hardware: kernel module, device nodes)
#   3) Layer 2       → pytest -m layer2  (containers: images, health, devices, env)
#   4) Layer 3       → pytest -m layer3  (pipeline: GStreamer, MJPEG stream)
#   5) Layer 4       → pytest -m layer4  (API contract: all endpoints)
#   6) Layer 5       → pytest -m layer5  (behavioral: brightness, saturation, switching)
#
# REQUIREMENTS
# ------------
# - docker and docker compose must be installed on the host.
# - The SCANBOX stack must already be running: docker compose up -d
# - The test_runner image must exist: docker compose build vid_mux_test
#   (test_runner reuses the scanbox_vid_mux_test image, no separate build needed)
#
# SNAPSHOT CLEANUP
# ----------------
# Passing tests delete the JPEG snapshots they created. Failing tests leave
# them in ./snapshots/ for visual inspection. Run this script again after
# fixing the issue to clean them up automatically.
#
# EXIT CODES
# ----------
# Inherits pytest's exit code:
#   0 — all selected tests passed
#   1 — one or more tests failed
#   2 — test collection error (syntax error, import error, etc.)
#   5 — no tests were collected (marker matched nothing)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# docker compose command (try the plugin form first, fall back to standalone)
if docker compose version &>/dev/null 2>&1; then
    DC="docker compose"
else
    DC="docker-compose"
fi

# Base compose invocation — always uses the "test" profile so test_runner is
# visible, and --no-deps so it does not try to recreate vid_mux_test.
DC_RUN="${DC} --profile test run --rm --no-deps test_runner"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

run_pytest() {
    # Run pytest inside the test_runner container with the given arguments.
    # All arguments are forwarded verbatim to the pytest invocation defined
    # in the test_runner entrypoint (see docker-compose.yml).
    #
    # Note: the entrypoint is already "python3 -m pytest tests/ -v --tb=short".
    # Arguments passed here are appended to that invocation, so passing
    # "-m layer1" results in "pytest tests/ -v --tb=short -m layer1".
    (cd "${SCRIPT_DIR}" && ${DC_RUN} "$@")
}

show_menu() {
    echo ""
    echo "╔══════════════════════════════════════╗"
    echo "║     SCANBOX Test Suite Runner        ║"
    echo "╠══════════════════════════════════════╣"
    echo "║  1) Run ALL layers                   ║"
    echo "║  2) Layer 1 — Hardware & host OS     ║"
    echo "║  3) Layer 2 — Container health       ║"
    echo "║  4) Layer 3 — GStreamer pipeline     ║"
    echo "║  5) Layer 4 — API contract           ║"
    echo "║  6) Layer 5 — Behavioral / visual    ║"
    echo "╚══════════════════════════════════════╝"
    echo ""
    printf "Select an option [1-6]: "
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [[ $# -gt 0 ]]; then
    # Arguments provided — pass them directly to pytest and exit.
    # This allows CI scripts and developers to bypass the menu entirely:
    #   ./run_tests.sh -m layer3
    #   ./run_tests.sh -k "test_snapshot" --tb=long
    run_pytest "$@"
    exit $?
fi

# No arguments — show the interactive menu.
show_menu
read -r choice

case "${choice}" in
    1)
        echo ""
        echo "Running all layers..."
        run_pytest
        ;;
    2)
        echo ""
        echo "Running Layer 1 — Hardware & host OS..."
        run_pytest -m layer1
        ;;
    3)
        echo ""
        echo "Running Layer 2 — Container health..."
        run_pytest -m layer2
        ;;
    4)
        echo ""
        echo "Running Layer 3 — GStreamer pipeline..."
        run_pytest -m layer3
        ;;
    5)
        echo ""
        echo "Running Layer 4 — API contract..."
        run_pytest -m layer4
        ;;
    6)
        echo ""
        echo "Running Layer 5 — Behavioral / visual..."
        run_pytest -m layer5
        ;;
    *)
        echo "Invalid option '${choice}'. Choose a number between 1 and 6." >&2
        exit 1
        ;;
esac
