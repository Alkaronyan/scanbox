"""
test_behavior.py — Layer 5: Behavioral and visual verification.

PURPOSE
-------
Verifies that camera controls and source switching have measurable, real-world
effects on the captured image frames. These tests go beyond contract checking
(Layer 4) to confirm that the pipeline actually changes the video signal in
response to control changes — not just that the API accepted the request.

WHAT IS CHECKED
---------------
1. Saturation control reduces color — sets saturation=255, captures a snapshot,
   sets saturation=0, captures another. The first snapshot must have a higher
   color saturation score than the second by at least 3 units. A score of 0 at
   saturation=0 means the camera is producing a grayscale image as expected.

2. Brightness control darkens the image — sets brightness=220, captures, sets
   brightness=30, captures. The bright frame must have a mean luminance at least
   30 units higher than the dark frame.

3. Mock source differs from physical — switches to physical camera, captures,
   switches to mock (SMPTE color bars), captures. The two frames must look
   visually different. Identical frames indicate the pipeline is not switching.

4. Consecutive source switches produce distinct frames — iterates through all
   sources, capturing one snapshot per source. Each consecutive pair of snapshots
   must look different. This is a comprehensive switching integrity test.

PHYSICAL SOURCES ONLY
---------------------
Tests 1 and 2 require a physical camera because the mock source (GStreamer
videotestsrc) does not expose V4L2 controls (brightness, saturation are V4L2
kernel-level controls, not GStreamer properties). These tests are skipped
automatically when no physical source is available.

SNAPSHOT NAMING AND CLEANUP
----------------------------
Each snapshot has a meaningful filename encoding the test name, source, and
parameter value, e.g. test_saturation_zero__src0__sat255.jpg. Snapshots are
deleted automatically after a passing test. On failure they remain on disk
at /home/Alfred/scanbox/snapshots/ for visual inspection.

THRESHOLDS AND SCENE DEPENDENCY
--------------------------------
Saturation threshold is 3 units (not 20) because the measured score depends
on scene content: a near-gray scene (white wall, gray table) produces a low
UAVG deviation even at saturation=255. The threshold confirms the control has
any measurable effect, not that the scene is colorful.

Brightness threshold is 30 units — more tolerant of scene variation because
luma change is proportional to the original brightness of the scene; a
very bright scene may compress the effective range of the brightness control.

TIMING CONSTRAINTS
------------------
A sleep of 1.1 seconds between the first and second snapshot in tests 1 and 2
ensures that the snapshot filenames are different (the default timestamp-based
fallback has 1-second resolution). Named filenames avoid this problem, but the
sleep also gives the camera's automatic exposure adjustment time to stabilize
after a control change before the frame is captured.

PRECONDITIONS
-------------
- Layers 1-4 must pass. In particular, the frame refresher thread in vid_mux
  must be running so that _last_frame is populated and reflects the current
  source after each switch.
- For tests 1 and 2: a physical USB camera with adjustable saturation and
  brightness V4L2 controls must be connected and active.
"""

import os
import time

import pytest

from tests.utils.image_analysis import (
    frames_are_different,
    jpeg_color_saturation,
    jpeg_mean_brightness,
)
from tests.utils.snapshot_helpers import snap_name


def _is_physical_source(source: dict) -> bool:
    """
    Return True if the source corresponds to a physical V4L2 camera device.

    Physical sources have a real device path (e.g. /dev/video100). The mock
    source uses /dev/video200 (v4l2loopback) which does not support V4L2
    user controls, so behavioral control tests must skip it.

    Args:
        source: Source dict with at least a 'device' key.

    Returns:
        True if device is set and is not /dev/video200.
    """
    device = source.get("device", "")
    return bool(device) and device != "/dev/video200"


def _take_snapshot(api_url, http_session, snapshots_dir, snapshot_collector, name=None) -> str:
    """
    Request a snapshot from the API with an optional named filename, register
    the resulting path in the collector, and return the absolute path.

    The named filename encodes test identity and parameters so that failed-test
    snapshots can be identified at a glance in the snapshots directory.

    Args:
        api_url: Base URL of the Flask API.
        http_session: Shared requests.Session.
        snapshots_dir: Absolute path to the host snapshots directory.
        snapshot_collector: Mutable list; path is appended for cleanup tracking.
        name: Optional filename for the saved JPEG (without directory prefix).

    Returns:
        Absolute path to the saved JPEG on disk.

    Raises:
        AssertionError: If the API returns a non-200 status, a non-success body,
            or the file does not exist on disk.
    """
    body = {"filename": name} if name else {}
    resp = http_session.post(f"{api_url}/api/v1/snapshot", json=body)
    assert resp.status_code == 200, f"Snapshot failed: HTTP {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "success", f"Snapshot status: {data}"
    path = os.path.join(snapshots_dir, data["filename"])
    assert os.path.exists(path), f"Snapshot file missing: {path}"
    snapshot_collector.append(path)
    return path


@pytest.mark.layer5
def test_saturation_zero_produces_grayscale(
    request, active_source_restored, sources, api_url, http_session,
    snapshots_dir, snapshot_collector,
):
    """
    Verify that setting saturation=0 measurably reduces color saturation
    compared to saturation=255.

    Captures two frames: one at maximum saturation (255) and one at zero
    saturation. The saturation score (|UAVG - 128| * 2 from ffmpeg signalstats)
    must be at least 3 units higher for the saturated frame. At saturation=0
    the camera produces a grayscale image (UAVG=128, score=0); at saturation=255
    any scene color produces a positive score.

    The threshold is 3 units rather than a larger value because the score is
    scene-dependent — a near-gray scene produces a low absolute score even at
    maximum saturation.

    After the test, saturation is restored to 128 (the typical default) in
    addition to the source being restored by active_source_restored.

    Args:
        request: pytest request object for generating the snapshot filename.
        active_source_restored: Fixture that restores the original active source.
        sources: List of source dicts from the sources session fixture.
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Path to the host snapshots directory.
        snapshot_collector: Collects paths; deletes them on test pass.

    Returns:
        None

    Raises:
        AssertionError: If the saturation=255 frame score is not at least 3 units
            higher than the saturation=0 frame score.
    """
    physical = [s for s in sources if _is_physical_source(s)]
    if not physical:
        pytest.skip("No physical camera source available for saturation test")

    src_id = physical[0]["id"]
    http_session.post(f"{api_url}/api/v1/source", json={"source_id": src_id})
    time.sleep(0.5)

    http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "saturation", "value": 255},
    )
    time.sleep(0.5)
    path_a = _take_snapshot(
        api_url, http_session, snapshots_dir, snapshot_collector,
        snap_name(request.node.name, src=src_id, sat=255),
    )

    http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "saturation", "value": 0},
    )
    time.sleep(1.1)  # >1s to guarantee unique timestamp in fallback filenames;
                     # also allows auto-exposure to stabilize after control change
    path_b = _take_snapshot(
        api_url, http_session, snapshots_dir, snapshot_collector,
        snap_name(request.node.name, src=src_id, sat=0),
    )

    sat_a = jpeg_color_saturation(path_a)
    sat_b = jpeg_color_saturation(path_b)

    http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "saturation", "value": 128},
    )

    assert sat_a > sat_b + 3, (
        f"Saturation=255 frame scored {sat_a:.1f}, saturation=0 frame scored {sat_b:.1f} — "
        f"difference of {sat_a - sat_b:.1f} is less than the 3-unit threshold"
    )


@pytest.mark.layer5
def test_brightness_change_affects_luminance(
    request, active_source_restored, sources, api_url, http_session,
    snapshots_dir, snapshot_collector,
):
    """
    Verify that changing brightness from 220 to 30 measurably darkens the image.

    Captures two frames: one at high brightness (220) and one at low brightness
    (30). The mean luma (YAVG from ffmpeg signalstats) of the bright frame must
    be at least 30 units higher than the dark frame. This confirms the brightness
    V4L2 control is wired through to the camera sensor and reflected in captured
    frames.

    After the test, brightness is restored to 128 (typical default) in addition
    to the source being restored by active_source_restored.

    Args:
        request: pytest request object for generating the snapshot filename.
        active_source_restored: Fixture that restores the original active source.
        sources: List of source dicts from the sources session fixture.
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Path to the host snapshots directory.
        snapshot_collector: Collects paths; deletes them on test pass.

    Returns:
        None

    Raises:
        AssertionError: If the bright frame's mean luma is not at least 30 units
            higher than the dark frame's mean luma.
    """
    physical = [s for s in sources if _is_physical_source(s)]
    if not physical:
        pytest.skip("No physical camera source available for brightness test")

    src_id = physical[0]["id"]
    http_session.post(f"{api_url}/api/v1/source", json={"source_id": src_id})
    time.sleep(0.5)

    http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "brightness", "value": 220},
    )
    time.sleep(0.5)
    path_a = _take_snapshot(
        api_url, http_session, snapshots_dir, snapshot_collector,
        snap_name(request.node.name, src=src_id, bri=220),
    )

    http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "brightness", "value": 30},
    )
    time.sleep(1.1)  # >1s to guarantee unique timestamp in fallback filenames;
                     # also allows auto-exposure to stabilize after control change
    path_b = _take_snapshot(
        api_url, http_session, snapshots_dir, snapshot_collector,
        snap_name(request.node.name, src=src_id, bri=30),
    )

    lum_a = jpeg_mean_brightness(path_a)
    lum_b = jpeg_mean_brightness(path_b)

    http_session.post(
        f"{api_url}/api/v1/camera/control",
        json={"control": "brightness", "value": 128},
    )

    assert lum_a > lum_b + 30, (
        f"Brightness=220 frame has luminance {lum_a:.1f}, "
        f"brightness=30 frame has {lum_b:.1f} — "
        f"difference of {lum_a - lum_b:.1f} is less than the 30-unit threshold"
    )


@pytest.mark.layer5
def test_mock_source_differs_from_physical(
    request, sources, active_source_restored, api_url, http_session,
    snapshots_dir, snapshot_collector,
):
    """
    Verify that the mock source (SMPTE color bars) produces visually different
    frames from the physical camera.

    Captures one frame from the physical camera and one from the mock source,
    then compares them using pixel-level analysis. The SMPTE color bar pattern
    is highly distinctive and should never be visually identical to a real
    camera image. If they appear identical the pipeline switch is not working.

    Skipped if only one source type is configured (e.g. no physical camera or
    no mock source present in the API source list).

    Args:
        request: pytest request object for generating snapshot filenames.
        sources: List of source dicts from the sources session fixture.
        active_source_restored: Fixture that restores the original active source.
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Path to the host snapshots directory.
        snapshot_collector: Collects paths; deletes them on test pass.

    Returns:
        None

    Raises:
        AssertionError: If the physical and mock frames appear visually identical.
    """
    physical = [s for s in sources if _is_physical_source(s)]
    mock = [s for s in sources if not _is_physical_source(s)]

    if not physical or not mock:
        pytest.skip("Need at least one physical and one mock source")

    http_session.post(f"{api_url}/api/v1/source", json={"source_id": physical[0]["id"]})
    time.sleep(1)
    path_physical = _take_snapshot(
        api_url, http_session, snapshots_dir, snapshot_collector,
        snap_name(request.node.name, src=physical[0]["id"], type="physical"),
    )

    http_session.post(f"{api_url}/api/v1/source", json={"source_id": mock[0]["id"]})
    time.sleep(1)
    path_mock = _take_snapshot(
        api_url, http_session, snapshots_dir, snapshot_collector,
        snap_name(request.node.name, src=mock[0]["id"], type="mock"),
    )

    assert frames_are_different(path_physical, path_mock), (
        "Physical camera frame and mock source frame are visually identical — "
        "pipeline may not be switching sources correctly"
    )


@pytest.mark.layer5
def test_snapshot_reflects_active_source(
    request, sources, active_source_restored, api_url, http_session,
    snapshots_dir, snapshot_collector,
):
    """
    Verify that consecutive source switches produce visually distinct snapshots.

    Cycles through all configured sources in order, capturing one snapshot per
    source after a 1-second settle time. Then checks every consecutive pair of
    snapshots — each pair must be visually different. A pair that appears identical
    means either the pipeline is not switching, or the snapshot endpoint returned
    a cached/stale frame instead of a fresh one from the new source.

    This is the most comprehensive switching test in the suite: it exercises
    every source transition, not just the first-to-last jump tested in Layer 3.

    Skipped if fewer than 2 sources are configured.

    Args:
        request: pytest request object for generating snapshot filenames.
        sources: List of source dicts from the sources session fixture.
        active_source_restored: Fixture that restores the original active source.
        api_url: Base API URL.
        http_session: Shared HTTP session.
        snapshots_dir: Path to the host snapshots directory.
        snapshot_collector: Collects paths; deletes them on test pass.

    Returns:
        None

    Raises:
        AssertionError: If any two consecutive source snapshots are visually
            identical, listing the source ID pairs that failed.
    """
    if len(sources) < 2:
        pytest.skip("Need at least 2 sources to compare consecutive snapshots")

    paths = []
    for i, source in enumerate(sources):
        http_session.post(
            f"{api_url}/api/v1/source",
            json={"source_id": source["id"]},
        )
        time.sleep(1)
        name = snap_name(request.node.name, src=source["id"], seq=i)
        paths.append(
            _take_snapshot(api_url, http_session, snapshots_dir, snapshot_collector, name)
        )

    identical_pairs = []
    for i in range(len(paths) - 1):
        if not frames_are_different(paths[i], paths[i + 1]):
            identical_pairs.append((sources[i]["id"], sources[i + 1]["id"]))

    assert not identical_pairs, (
        f"Consecutive source pairs produced identical snapshots: {identical_pairs} — "
        "pipeline may be stuck or snapshot endpoint is returning stale frames"
    )
