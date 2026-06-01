"""
test_hardware.py — Layer 1: Hardware and host OS prerequisite checks.

PURPOSE
-------
Verifies that the physical infrastructure required by the SCANBOX system
exists and is accessible before any container or API test is attempted.
These are the lowest-level checks in the suite: if any of them fail, every
upper layer will also fail (containers can't start, GStreamer can't open
devices, API returns no sources).

WHAT IS CHECKED
---------------
1. v4l2loopback kernel module — must be loaded for /dev/video200 to exist.
   The vid_mux_test container loads this module at startup via modprobe;
   if it hasn't loaded yet, the mock camera is unavailable.

2. /dev/video200 — the loopback device created by v4l2loopback with
   video_nr=200. The mock SMPTE stream is written to this device by the
   vid_mux_test container and read by vid_mux's GStreamer pipeline.

3. Physical camera by-id symlinks — /dev/v4l/by-id/*-video-index0 entries
   created by udev for each connected USB camera. These stable symlinks are
   used (not /dev/videoN) to avoid index reordering after reboots.

4. Physical camera readability — confirms the device nodes are accessible
   by the current user/group, not just that they exist. A device that exists
   but is not readable would cause GStreamer to fail silently.

EXECUTION CONTEXT
-----------------
These tests run inside the test_runner container with:
- privileged: true  → removes cgroup device whitelist, /dev/* is visible
- /dev/v4l:/dev/v4l:ro bind mount → udev by-id symlinks are available
  (privileged alone does not expose these symlinks)
- Linux containers share the host kernel → lsmod reads /proc/modules from
  the real kernel, so kernel module checks are accurate from inside a container.

PRECONDITIONS
-------------
- The vid_mux_test container must have completed its startup sequence
  (which loads v4l2loopback and creates /dev/video200) before these tests run.
- At least one physical USB camera must be connected to the Raspberry Pi.
"""

import glob
import os
import subprocess

import pytest


@pytest.mark.layer1
def test_v4l2loopback_loaded():
    """
    Verify that the v4l2loopback kernel module is loaded on the host.

    v4l2loopback creates virtual V4L2 devices that can be written to by one
    process and read from by another. SCANBOX uses it to expose the synthetic
    SMPTE test pattern from the vid_mux_test container as a V4L2 device
    (/dev/video200) that vid_mux's GStreamer pipeline can read.

    Without this module, /dev/video200 cannot exist and the mock source fails.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If "v4l2loopback" is absent from lsmod output.
    """
    result = subprocess.run(["lsmod"], capture_output=True, text=True)
    assert "v4l2loopback" in result.stdout, (
        "v4l2loopback kernel module is not loaded — "
        "run: sudo modprobe v4l2loopback video_nr=200 card_label=MockCam"
    )


@pytest.mark.layer1
def test_mock_device_exists():
    """
    Verify that the loopback mock video device /dev/video200 exists on the host.

    /dev/video200 is created by v4l2loopback when loaded with video_nr=200.
    The vid_mux_test container loads the module with this parameter at startup.
    If this device is absent, the mock camera source is unavailable and any
    test that switches to the mock source will fail.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If /dev/video200 does not exist.
    """
    assert os.path.exists("/dev/video200"), (
        "/dev/video200 does not exist — v4l2loopback may not be loaded or "
        "was loaded without video_nr=200"
    )


@pytest.mark.layer1
def test_at_least_one_physical_camera():
    """
    Verify that at least one physical USB camera is connected and enumerated by udev.

    udev creates stable by-id symlinks under /dev/v4l/by-id/ for each V4L2
    device. The *-video-index0 suffix identifies the primary capture interface
    (as opposed to metadata or secondary interfaces). SCANBOX uses these paths
    to map cameras into the vid_mux container deterministically.

    Without at least one physical camera, the pipeline has no real input and
    all physical-source tests will be skipped or fail.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If no *-video-index0 symlinks exist under /dev/v4l/by-id/.
    """
    cameras = glob.glob("/dev/v4l/by-id/*-video-index0")
    assert len(cameras) >= 1, (
        "No physical cameras found at /dev/v4l/by-id/*-video-index0 — "
        f"check USB connections (found: {cameras})"
    )


@pytest.mark.layer1
def test_physical_camera_devices_readable():
    """
    Verify that each physical camera device node is readable by the current process.

    Existence of the device node is not sufficient — the process (and therefore
    the container) must also have read permission. Without read access GStreamer
    will fail to open the device, producing a cryptic pipeline error rather than
    a clear permission-denied message.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If any camera device node is not readable.
    """
    cameras = glob.glob("/dev/v4l/by-id/*-video-index0")
    if not cameras:
        pytest.skip("No physical cameras found — skipping readability check")

    not_readable = []
    for cam_symlink in cameras:
        real = os.path.realpath(cam_symlink)   # resolve symlink to actual /dev/videoN
        if not os.access(real, os.R_OK):
            not_readable.append(real)

    assert not not_readable, (
        f"Camera device(s) exist but are not readable: {not_readable} — "
        "check group membership (video group) or udev rules"
    )
