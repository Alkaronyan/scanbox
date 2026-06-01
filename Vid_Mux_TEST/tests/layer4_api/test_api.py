"""
test_api.py — Layer 4: REST API contract verification.

PURPOSE
-------
Verifies that every public endpoint of the SCANBOX Flask API returns the
correct HTTP status codes and response body structure. These are contract
tests — they check the API's interface, not its visual or behavioral effects
(that is Layer 5). A failure here means the API has broken its contract with
clients (the browser UI and any external integrations).

ENDPOINTS COVERED
-----------------
GET  /api/v1/status
  - Returns HTTP 200 with a JSON body containing active_source (int) and
    sources (non-empty list of dicts). The active_source value must be one
    of the IDs in the sources list (internal consistency check).

POST /api/v1/source
  - Accepts {"source_id": int}. Returns HTTP 200 and status=success for any
    valid source ID. Returns HTTP 400 for an invalid source ID (e.g. 999).
    This confirms input validation is in place.

POST /api/v1/snapshot
  - Returns HTTP 200 with status=success and a filename field. The file named
    in the response must exist on disk (confirming the API actually writes it).

GET  /api/v1/snapshot/last
  - Returns HTTP 200 with Content-Type: image/jpeg. Confirms the route serves
    a real JPEG binary, not a JSON error or empty body.

GET  /api/v1/camera/controls
  - Returns HTTP 200 with definitions (list) and controls (dict) keys.
    When a physical source is active, each definition object must contain
    name, label, and type — the minimum fields required for the UI to render
    a control widget.

POST /api/v1/camera/control
  - Accepts {"control": str, "value": int}. Reads the current saturation
    value, sets it to a different value, then restores it. Confirms the
    round-trip works (set succeeds, restore succeeds). Skipped if no physical
    source is available (mock sources have no V4L2 controls).

GET  /stream
  - Returns HTTP 200 with Content-Type containing multipart/x-mixed-replace,
    which is the standard MJPEG streaming content type expected by browsers.

PRECONDITIONS
-------------
- Layer 2 must pass (vid_mux running, port 80 accepting connections).
- Layer 3 should pass (pipeline producing frames; snapshot endpoint needs
  _last_frame to be populated, which requires the frame refresher thread).
"""

import os

import pytest


@pytest.mark.layer4
def test_status_returns_ok(api_url, http_session):
    """
    Verify that GET /api/v1/status returns HTTP 200.

    This is the most basic connectivity check for the API layer. If this
    fails, all other Layer 4 tests will also fail because the API is
    unreachable or returning an unexpected error code at startup.

    Args:
        api_url: Base API URL from the api_url session fixture.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If the response status code is not 200.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    assert resp.status_code == 200, (
        f"GET /api/v1/status returned HTTP {resp.status_code}"
    )


@pytest.mark.layer4
def test_status_has_sources_list(api_url, http_session):
    """
    Verify that the status response body contains a 'sources' key.

    The sources list is the primary discovery mechanism for clients: the UI
    reads it to populate the source selector. If the key is absent the UI
    cannot determine which cameras are available.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If 'sources' is missing from the response JSON.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    data = resp.json()
    assert "sources" in data, (
        f"'sources' key missing from status response: {data}"
    )


@pytest.mark.layer4
def test_status_sources_not_empty(api_url, http_session):
    """
    Verify that the sources list in the status response is non-empty.

    An empty sources list means the pipeline started with no configured
    cameras, either because SCANBOX_SOURCES was not set or was empty.
    The UI would show no sources and the operator could not switch inputs.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If the sources list is empty.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    data = resp.json()
    assert len(data.get("sources", [])) >= 1, (
        "Status response has an empty sources list — "
        "check SCANBOX_SOURCES env var inside vid_mux"
    )


@pytest.mark.layer4
def test_status_active_source_in_sources(api_url, http_session):
    """
    Verify that the active_source ID reported in status exists in the sources list.

    active_source and sources are maintained in the same Flask process. If they
    diverge it indicates a state management bug (e.g. a switch operation updated
    one but not the other). A client that trusts active_source is in sources
    would crash trying to look up the active source's name or device.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If active_source is not present in the sources list.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    data = resp.json()
    active = data.get("active_source")
    source_ids = [s["id"] for s in data.get("sources", [])]
    assert active in source_ids, (
        f"active_source={active} is not in sources list {source_ids} — "
        "internal state inconsistency in the API"
    )


@pytest.mark.layer4
def test_switch_to_each_source(sources, active_source_restored, api_url, http_session):
    """
    Verify that POST /api/v1/source succeeds for every source in the source list.

    Iterates through all sources returned by the API and switches to each one.
    Each switch must return HTTP 200 and status=success. A failure means the
    API rejected a valid source ID — either the source list and the switcher
    are out of sync, or the GStreamer pipeline reported an error.

    The active_source_restored fixture restores the original source after
    this test, preventing source state from leaking into subsequent tests.

    Args:
        sources: List of source dicts from the sources session fixture.
        active_source_restored: Fixture that restores the original source afterward.
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If any source switch returns a non-200 response or
            a response body without status=success.
    """
    for source in sources:
        src_id = source["id"]
        resp = http_session.post(
            f"{api_url}/api/v1/source",
            json={"source_id": src_id},
        )
        assert resp.status_code == 200, (
            f"Switch to source {src_id} returned HTTP {resp.status_code}"
        )
        assert resp.json().get("status") == "success", (
            f"Switch to source {src_id} did not return status=success: {resp.json()}"
        )


@pytest.mark.layer4
def test_switch_invalid_source_returns_error(api_url, http_session):
    """
    Verify that switching to a non-existent source_id returns HTTP 400.

    Input validation must reject IDs that are not in the configured source list.
    Without this check a client bug or malicious request could trigger undefined
    behaviour in the GStreamer pipeline by passing an unknown source ID to the
    switcher element.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If the API returns HTTP 200 for source_id=999, indicating
            missing input validation.
    """
    resp = http_session.post(
        f"{api_url}/api/v1/source",
        json={"source_id": 999},
    )
    assert resp.status_code == 400, (
        f"POST /api/v1/source with source_id=999 returned HTTP {resp.status_code}; "
        "expected 400 — input validation may be missing"
    )


@pytest.mark.layer4
def test_snapshot_returns_success(api_url, http_session):
    """
    Verify that POST /api/v1/snapshot returns HTTP 200 with status=success.

    The snapshot endpoint reads _last_frame (maintained by the frame refresher
    background thread) and writes it to disk. A failure here means either the
    frame refresher has not populated _last_frame yet, or the disk write failed.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If the snapshot endpoint returns a non-200 status or
            a body without status=success.
    """
    resp = http_session.post(f"{api_url}/api/v1/snapshot")
    assert resp.status_code == 200, (
        f"POST /api/v1/snapshot returned HTTP {resp.status_code}"
    )
    assert resp.json().get("status") == "success", (
        f"Snapshot response status != success: {resp.json()}"
    )


@pytest.mark.layer4
def test_snapshot_file_created(api_url, http_session, snapshots_dir):
    """
    Verify that a JPEG file actually appears on disk after POST /api/v1/snapshot.

    The API returns the filename in the response body. This test checks that
    the file exists at that path in the bind-mounted snapshots directory. A
    missing file would mean the API lied about success, or the bind mount path
    is wrong, or the disk is full.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Absolute path to the host snapshots directory.

    Returns:
        None

    Raises:
        AssertionError: If the snapshot file named in the response does not
            exist on the host filesystem.
    """
    resp = http_session.post(f"{api_url}/api/v1/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    filename = data.get("filename")
    assert filename, f"No filename in snapshot response: {data}"
    path = os.path.join(snapshots_dir, filename)
    assert os.path.exists(path), (
        f"Snapshot file does not exist on host: {path} — "
        "check the bind mount for /exports/snapshots in docker-compose.yml"
    )


@pytest.mark.layer4
def test_snapshot_last_returns_jpeg(api_url, http_session):
    """
    Verify that GET /api/v1/snapshot/last returns Content-Type: image/jpeg.

    This endpoint serves the most recently saved snapshot as a raw JPEG binary.
    The UI uses it to display the last captured image. If Content-Type is wrong
    (e.g. application/json because there are no snapshots yet), the browser will
    not render the image.

    This test first creates a snapshot to ensure at least one exists, then
    verifies the Content-Type of the last-snapshot endpoint.

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If the endpoint returns a non-200 status or a
            Content-Type that does not include image/jpeg.
    """
    http_session.post(f"{api_url}/api/v1/snapshot")  # ensure at least one snapshot exists
    resp = http_session.get(f"{api_url}/api/v1/snapshot/last")
    assert resp.status_code == 200, (
        f"GET /api/v1/snapshot/last returned HTTP {resp.status_code}"
    )
    content_type = resp.headers.get("Content-Type", "")
    assert "image/jpeg" in content_type, (
        f"Expected Content-Type image/jpeg, got: {content_type}"
    )


@pytest.mark.layer4
def test_camera_controls_returns_definitions(api_url, http_session):
    """
    Verify that GET /api/v1/camera/controls returns both 'definitions' and 'controls' keys.

    definitions is a list of control descriptors (one per V4L2 control) used
    by the UI to render sliders and toggles. controls is a dict mapping control
    names to their current integer values. Both must always be present, even
    when the active source is the mock camera (in which case both are empty).

    Args:
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If either key is missing from the response.
    """
    resp = http_session.get(f"{api_url}/api/v1/camera/controls")
    assert resp.status_code == 200, (
        f"GET /api/v1/camera/controls returned HTTP {resp.status_code}"
    )
    data = resp.json()
    assert "definitions" in data, (
        f"'definitions' key missing from camera controls response: {data}"
    )
    assert "controls" in data, (
        f"'controls' key missing from camera controls response: {data}"
    )


@pytest.mark.layer4
def test_camera_controls_definition_structure(sources, active_source_restored, api_url, http_session):
    """
    When a physical source is active, verify that each control definition
    contains the required fields: name, label, and type.

    The UI uses these three fields to render each control: name is the V4L2
    key sent to POST /api/v1/camera/control, label is the human-readable
    display string, and type (int/bool/menu) determines which widget to show.
    A definition missing any of these fields would cause the UI to crash or
    render an unusable control.

    Skipped if no physical source is available or if the physical source
    reports no controls (some cameras have no adjustable V4L2 controls).

    Args:
        sources: List of source dicts from the sources session fixture.
        active_source_restored: Fixture that restores the original source afterward.
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If any definition object is missing name, label, or type.
    """
    physical = [
        s for s in sources
        if s.get("device") and s["device"] != "/dev/video200"
    ]
    if not physical:
        pytest.skip("No physical source available to check control definition structure")

    http_session.post(
        f"{api_url}/api/v1/source",
        json={"source_id": physical[0]["id"]},
    )

    resp = http_session.get(f"{api_url}/api/v1/camera/controls")
    assert resp.status_code == 200
    defs = resp.json().get("definitions", [])

    if not defs:
        pytest.skip("Physical source reports no control definitions")

    required = {"name", "label", "type"}
    bad = [d for d in defs if not required.issubset(d.keys())]
    assert not bad, (
        f"Control definitions missing required fields {required}: "
        + ", ".join(str(d) for d in bad)
    )


@pytest.mark.layer4
def test_camera_control_set_and_restore(active_source_restored, api_url, http_session):
    """
    Verify that POST /api/v1/camera/control can set a V4L2 control and
    that the operation returns status=success.

    Reads the current saturation value, sets it to a different value, then
    restores the original. Confirms the full round-trip: read current value,
    set new value, restore original. Skipped if the active source is the mock
    camera (which has no V4L2 controls, since it uses GStreamer videotestsrc
    internally rather than a real V4L2 device).

    The active_source_restored fixture ensures the source is returned to its
    original state after the test regardless of outcome.

    Args:
        active_source_restored: Fixture that restores the original active source.
        api_url: Base API URL.
        http_session: Shared HTTP session.

    Returns:
        None

    Raises:
        AssertionError: If setting or restoring the saturation control fails.
    """
    ctrl_resp = http_session.get(f"{api_url}/api/v1/camera/controls")
    data = ctrl_resp.json()
    controls = data.get("controls", {})

    if "saturation" not in controls:
        pytest.skip("saturation control not available for active source (mock/synthetic)")

    original = controls["saturation"]
    new_value = 100 if original != 100 else 80

    set_resp = http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "saturation", "value": new_value},
    )
    assert set_resp.status_code == 200, (
        f"Set saturation={new_value} returned HTTP {set_resp.status_code}"
    )
    assert set_resp.json().get("status") == "success", (
        f"Set saturation did not return success: {set_resp.json()}"
    )

    restore_resp = http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "saturation", "value": original},
    )
    assert restore_resp.status_code == 200, (
        f"Restore saturation={original} returned HTTP {restore_resp.status_code}"
    )


@pytest.mark.layer4
def test_stream_returns_multipart(api_url):
    """
    Verify that GET /stream responds with Content-Type multipart/x-mixed-replace.

    multipart/x-mixed-replace is the standard content type for MJPEG streams.
    Browsers use this content type to continuously update the displayed image
    as new frames arrive. An incorrect content type (e.g. application/octet-stream)
    would cause browsers to download the stream as a file instead of displaying it.

    The connection is opened in streaming mode and immediately closed after
    reading the response headers to avoid consuming the full stream.

    Args:
        api_url: Base API URL.

    Returns:
        None

    Raises:
        AssertionError: If Content-Type does not contain multipart/x-mixed-replace.
    """
    import requests as req
    resp = req.get(f"{api_url}/stream", stream=True, timeout=5)
    content_type = resp.headers.get("Content-Type", "")
    resp.close()
    assert "multipart/x-mixed-replace" in content_type, (
        f"Expected multipart/x-mixed-replace in Content-Type, got: {content_type}"
    )
