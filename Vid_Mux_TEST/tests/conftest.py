"""
conftest.py — Shared pytest fixtures for the SCANBOX test suite.

PURPOSE
-------
Defines session-scoped and function-scoped fixtures that are automatically
available to every test in every layer without explicit imports. pytest
discovers this file by its name and injects the fixtures by parameter name.

FIXTURE OVERVIEW
----------------
api_url (session):
    Base URL of the vid_mux Flask API. Read from the SCANBOX_API_URL
    environment variable so the test_runner container can target the API
    on the host network without hardcoding addresses.

http_session (session):
    A shared requests.Session with a 5-second timeout applied to every
    call. Reused across all tests to avoid the overhead of creating a new
    TCP connection per test.

snapshots_dir (session):
    Absolute path to the host directory where the API saves JPEG snapshots
    (/home/Alfred/scanbox/snapshots, bind-mounted into both the vid_mux and
    test_runner containers at the same path). Layer 3, 4, and 5 tests use
    this path to verify that snapshot files actually appear on disk.

sources (session):
    List of source dicts from GET /api/v1/status, shape:
    [{"id": int, "name": str, "device": str}, ...].
    Fetched once per session and shared. Tests that iterate over sources
    (Layer 3, Layer 5) consume this fixture directly.

snapshot_collector (function):
    A mutable list that tests append snapshot file paths to via
    _take_snapshot(). After the test finishes, the fixture checks whether
    the test passed (via the rep_call attribute set by the
    pytest_runtest_makereport hook below). If the test passed, all
    collected snapshot files are deleted — keeping the snapshots directory
    clean during normal runs. If the test failed, the files are left on
    disk so the developer can inspect the actual JPEG frames.

active_source_restored (function):
    Saves the currently active source ID before the test starts, then
    restores it via POST /api/v1/source after the test ends (whether it
    passes or fails). Used by Layer 4 and Layer 5 tests that switch
    sources mid-test, ensuring the system is left in its original state.

PASS/FAIL HOOK
--------------
pytest_runtest_makereport is a pytest hook that runs after each test phase
(setup / call / teardown). It stores the result of the "call" phase on the
test item as `item.rep_call`. The snapshot_collector fixture reads this
attribute to decide whether to clean up.

HOW TESTS RUN
-------------
All tests run inside the `test_runner` Docker container, which has:
- network_mode: host  → reaches Flask API at localhost:80
- bind mount of /home/Alfred/scanbox  → snapshot file paths resolve identically
- /var/run/docker.sock mounted  → Layer 2 tests can inspect the host daemon
- privileged: true  → Layer 1 tests can read /proc/modules and /dev/*
"""

import os
import pytest
import requests


# ---------------------------------------------------------------------------
# Pass/fail tracking — sets item.rep_call after each test so that the
# snapshot_collector fixture can inspect the outcome and decide whether to
# delete the snapshots it registered during the test.
# ---------------------------------------------------------------------------

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_url():
    """
    Base URL of the SCANBOX Flask API.

    Read from the SCANBOX_API_URL environment variable so the test_runner
    container can be pointed at different API instances without code changes.
    Defaults to http://localhost:80, which works when network_mode is host.

    Returns:
        str: Base URL, e.g. "http://localhost:80".
    """
    return os.environ.get("SCANBOX_API_URL", "http://localhost:80")


@pytest.fixture(scope="session")
def http_session():
    """
    Shared requests.Session with a 5-second timeout on every request.

    A single session is reused across the entire test run to avoid repeated
    TCP connection setup. The timeout prevents tests from hanging indefinitely
    if the API becomes unresponsive.

    Returns:
        requests.Session: Configured session object.
    """
    session = requests.Session()
    session.request = lambda method, url, **kwargs: requests.Session.request(
        session, method, url, timeout=kwargs.pop("timeout", 5), **kwargs
    )
    return session


@pytest.fixture(scope="session")
def snapshots_dir():
    """
    Absolute path to the SCANBOX snapshots directory on the host filesystem.

    Both the vid_mux container (which writes snapshots) and the test_runner
    container (which verifies them) see this path because /home/Alfred/scanbox
    is bind-mounted at the same host path in both containers.

    Returns:
        str: "/home/Alfred/scanbox/snapshots"
    """
    return "/home/Alfred/scanbox/snapshots"


@pytest.fixture(scope="session")
def sources(api_url, http_session):
    """
    List of camera source dicts from GET /api/v1/status, fetched once per session.

    Each entry has the shape: {"id": int, "name": str, "device": str}.
    Physical cameras have a device path like "/dev/video100"; the mock source
    has "/dev/video200". This distinction is used by Layer 5 tests to skip
    behavioral checks when only the mock source is available.

    Returns:
        list[dict]: Non-empty list of source objects.

    Raises:
        AssertionError: If the API is unreachable or returns zero sources,
            which would cause all Layer 3-5 tests to fail with misleading errors.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    assert resp.status_code == 200, f"GET /api/v1/status returned {resp.status_code}"
    data = resp.json()
    assert "sources" in data, "Status response has no 'sources' key"
    assert len(data["sources"]) >= 1, "API reports zero sources"
    return data["sources"]


@pytest.fixture
def snapshot_collector(request):
    """
    Mutable list that collects snapshot file paths created during a test.

    Tests append paths to this list via their local _take_snapshot() helper.
    After the test finishes:
    - If the test PASSED: all collected files are deleted (clean run).
    - If the test FAILED: files are left on disk for post-mortem inspection.

    This makes it easy to examine the actual JPEG frames when a visual
    assertion fails (e.g. "frames should differ but didn't").

    Yields:
        list[str]: Empty list at start; populated by _take_snapshot() calls.
    """
    paths: list[str] = []
    yield paths
    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.passed:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


@pytest.fixture
def active_source_restored(api_url, http_session):
    """
    Save the active source before the test and restore it afterward.

    Layer 4 and Layer 5 tests switch between sources mid-test. This fixture
    ensures the system is left in its original state regardless of whether
    the test passes, fails, or raises an unexpected exception.

    The restore happens in the fixture teardown (after `yield`), which pytest
    guarantees runs even if the test body raises.

    Yields:
        None

    Raises:
        AssertionError: If the initial GET /api/v1/status call fails, which
            would indicate the API is down before the test even starts.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    assert resp.status_code == 200
    original_id = resp.json().get("active_source")
    yield
    if original_id is not None:
        http_session.post(
            f"{api_url}/api/v1/source",
            json={"source_id": original_id},
        )
