"""
test_pipeline.py — Layer 3: GStreamer pipeline verification.

PURPOSE
-------
Verifies that the GStreamer pipeline inside vid_mux is operational: that it
can produce real JPEG frames from every configured source and that switching
between sources results in visually distinct frames. These tests use the
snapshot API endpoint as the frame extraction mechanism — they do not
interact with GStreamer directly.

WHAT IS CHECKED
---------------
1. Each source produces a real snapshot — for every source in the API's source
   list, the test switches to it, waits 1 second for the pipeline to settle,
   and requests a snapshot. The resulting JPEG must be larger than 5 KB. A
   near-empty file means GStreamer produced a blank or error frame.

2. Switching produces different frames — switches from source 0 to the last
   source and compares the two snapshots using pixel-level analysis. If the
   frames look identical, the pipeline is not actually switching inputs (e.g.
   videomux element stuck, appsink getting stale frames).

3. MJPEG stream delivers real data — reads 50 KB from GET /stream and checks
   that the MJPEG boundary marker "--frame" is present. This confirms the
   streaming path (separate from the snapshot path) is functional.

HOW SNAPSHOTS ARE NAMED
-----------------------
Each snapshot is given a meaningful filename via the snapshot_collector
fixture and snap_name() helper:
  test_each_source_produces_snapshot__src0.jpg
  test_switching_produces_different_frames__src0__framea.jpg

On test pass the files are deleted automatically. On failure they remain on
disk for inspection at /home/Alfred/scanbox/snapshots/.

PRECONDITIONS
-------------
- Layer 2 must pass (vid_mux running, devices mapped, port 80 listening).
- The GStreamer pipeline must have fully initialized (the frame refresher
  background thread must have received at least one frame from the appsink).
  The 1-second sleep after each source switch covers normal pipeline settle time.

KEY DESIGN NOTE — APPSINK BACKPRESSURE
---------------------------------------
GStreamer's appsink element applies backpressure to the pipeline when its
buffer is full and no downstream consumer is draining it. The vid_mux
application uses a permanent background "frame refresher" thread that
continuously drains the appsink queue, preventing stalls. Without this thread,
_last_frame would become stale after the first stream consumer disconnects
and all subsequent snapshot requests would return the same frozen frame.
"""

import os
import time

import pytest
import requests

from tests.utils.image_analysis import frames_are_different, jpeg_filesize
from tests.utils.snapshot_helpers import snap_name


def _take_snapshot(api_url, http_session, snapshots_dir, snapshot_collector, name=None) -> str:
    """
    Request a snapshot from the API, register it in the collector, and return
    its absolute path on disk.

    Passes the optional name as the filename field in the POST body so the API
    saves the file under that name instead of its default timestamp-based name.
    The path is appended to snapshot_collector so the fixture can clean it up
    after the test completes (on pass only).

    Args:
        api_url: Base URL of the Flask API.
        http_session: Shared requests.Session.
        snapshots_dir: Absolute path to the host snapshots directory.
        snapshot_collector: Mutable list from the snapshot_collector fixture.
        name: Optional filename (without directory path) for the saved JPEG.

    Returns:
        Absolute path to the saved JPEG file on disk.

    Raises:
        AssertionError: If the API returns a non-200 status, a non-success
            body, or the file does not exist on disk after the call.
    """
    body = {"filename": name} if name else {}
    resp = http_session.post(f"{api_url}/api/v1/snapshot", json=body)
    assert resp.status_code == 200, f"Snapshot failed: HTTP {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Snapshot status: {data}"
    path = os.path.join(snapshots_dir, data["filename"])
    assert os.path.exists(path), f"Snapshot file missing on disk: {path}"
    snapshot_collector.append(path)
    return path


@pytest.mark.layer3
def test_each_source_produces_snapshot(
    request, sources, active_source_restored, api_url, http_session,
    snapshots_dir, snapshot_collector,
):
    """
    Verify that every configured source produces a real JPEG snapshot (> 5 KB).

    For each source returned by the API, this test: switches to it, waits 1
    second for the pipeline to stabilize on the new input, then requests a
    snapshot. A JPEG smaller than 5 KB indicates a blank, black, or corrupt
    frame — typically caused by GStreamer failing to open the device or the
    pipeline producing an error frame.

    Args:
        request: pytest request object; provides test name for filename generation.
        sources: List of source dicts from the sources session fixture.
        active_source_restored: Fixture that restores the original source after the test.
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Path to the host snapshots directory.
        snapshot_collector: Collects snapshot paths; deletes them on test pass.

    Returns:
        None

    Raises:
        AssertionError: If any source switch fails, snapshot fails, or the
            resulting file is smaller than 5 KB.
    """
    for source in sources:
        src_id = source["id"]

        switch_resp = http_session.post(
            f"{api_url}/api/v1/source",
            json={"source_id": src_id},
        )
        assert switch_resp.status_code == 200, (
            f"Switch to source {src_id} failed: HTTP {switch_resp.status_code}"
        )

        time.sleep(1)  # allow GStreamer pipeline to settle on the new source

        name = snap_name(request.node.name, src=src_id)
        path = _take_snapshot(api_url, http_session, snapshots_dir, snapshot_collector, name)

        size = jpeg_filesize(path)
        assert size > 5000, (
            f"Source {src_id} snapshot is only {size} bytes (<5 KB) — "
            "likely a blank or corrupt frame from GStreamer"
        )


@pytest.mark.layer3
def test_switching_produces_different_frames(
    request, sources, active_source_restored, api_url, http_session,
    snapshots_dir, snapshot_collector,
):
    """
    Verify that switching between two sources produces visually distinct frames.

    Takes a snapshot from source 0, switches to the last source, takes another
    snapshot, then compares the two using pixel-level analysis via ffmpeg's
    blend filter. If the frames look identical (mean pixel difference below
    threshold), the pipeline is not actually switching inputs.

    This test requires at least 2 sources. It is skipped if only one source
    is configured (e.g. single-camera setup with no mock).

    Args:
        request: pytest request object; provides test name for filename generation.
        sources: List of source dicts from the sources session fixture.
        active_source_restored: Fixture that restores the original source after the test.
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Path to the host snapshots directory.
        snapshot_collector: Collects snapshot paths; deletes them on test pass.

    Returns:
        None

    Raises:
        AssertionError: If the two frames appear visually identical, indicating
            the pipeline may be stuck or returning stale frames.
    """
    if len(sources) < 2:
        pytest.skip("Need at least 2 sources to test switching")

    def take(src_id, label):
        http_session.post(f"{api_url}/api/v1/source", json={"source_id": src_id})
        time.sleep(1)
        name = snap_name(request.node.name, src=src_id, frame=label)
        return _take_snapshot(api_url, http_session, snapshots_dir, snapshot_collector, name)

    path_a = take(sources[0]["id"], "a")
    path_b = take(sources[-1]["id"], "b")

    assert frames_are_different(path_a, path_b), (
        "Frames from source 0 and last source look identical — "
        "pipeline may not be switching correctly or appsink is returning stale frames"
    )


@pytest.mark.layer3
def test_stream_endpoint_delivers_frames(api_url):
    """
    Verify that GET /stream delivers at least 50 KB of MJPEG data within 5 seconds,
    and that the data contains the MJPEG boundary marker '--frame'.

    The MJPEG stream is the primary real-time video path used by the browser UI.
    This test opens the stream, reads up to 50 KB, then closes the connection.
    It confirms both that the stream is alive (data flows) and that the MJPEG
    framing is correct (boundary markers present) — a client without boundary
    markers would display a corrupt or frozen image.

    This test does not use the snapshot_collector because it does not create
    any files on disk.

    Args:
        api_url: Base API URL.

    Returns:
        None

    Raises:
        AssertionError: If the stream times out before delivering 50 KB, or
            if the MJPEG boundary marker is absent in the received data.
    """
    try:
        resp = requests.get(f"{api_url}/stream", stream=True, timeout=5)
        assert resp.status_code == 200, (
            f"GET /stream returned HTTP {resp.status_code}"
        )

        data = b""
        deadline = time.time() + 5
        for chunk in resp.iter_content(chunk_size=4096):
            data += chunk
            if len(data) >= 50 * 1024:
                break
            if time.time() > deadline:
                break
        resp.close()
    except requests.exceptions.Timeout:
        raise AssertionError("GET /stream timed out before delivering 50 KB")

    assert len(data) >= 50 * 1024, (
        f"Stream delivered only {len(data)} bytes in 5s (expected >= 50 KB) — "
        "GStreamer pipeline may not be producing frames"
    )
    assert b"--frame" in data, (
        "MJPEG boundary marker '--frame' not found in stream data — "
        "MJPEG framing may be broken"
    )
