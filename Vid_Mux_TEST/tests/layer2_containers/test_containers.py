"""
test_containers.py — Layer 2: Docker stack health verification.

PURPOSE
-------
Confirms that the full SCANBOX Docker stack is correctly built, running, and
configured before any pipeline or API test is attempted. These checks catch
the most common operational problems: a container that crashed at startup, a
missing device mapping, or a misconfigured environment variable.

WHAT IS CHECKED
---------------
1. Docker images exist — both scanbox-vid_mux and scanbox_vid_mux_test must
   be present in the local image cache. Missing images mean the containers
   cannot start and all subsequent tests fail with connection errors.

2. Physical device nodes in vid_mux — for every physical source reported by
   the API, its device path (e.g. /dev/video100) must exist inside the vid_mux
   container. If a --device mapping in docker-compose.yml is wrong or the
   camera was disconnected, GStreamer cannot open the device.

3. scanbox_dhcp running — the DHCP server for the USB NCM link must be alive.
   Without it the Windows PC cannot get an IP address and cannot reach the API.

4. vid_mux_test running — the mock camera container must be running. It holds
   the v4l2loopback module loaded and streams the synthetic SMPTE pattern to
   /dev/video200. If it stops, the mock source disappears.

5. vid_mux running — the Flask API + GStreamer pipeline container. All Layer 3,
   4, and 5 tests depend on it being alive.

6. Port 80 listening — a TCP connection test to localhost:80. Even if vid_mux
   is running, a startup error in Flask or GStreamer can cause it to exit
   immediately; this check confirms the API is actually accepting connections.

7. /dev/video100 in vid_mux — the physical camera device mapped via
   docker-compose devices. GStreamer reads from this path.

8. /dev/video200 in vid_mux — the loopback mock device. Also mapped via
   devices in docker-compose.

9. SCANBOX_SOURCES env var — must be set inside vid_mux and must be valid JSON
   with at least 2 entries (one physical + one mock). The Flask API parses this
   at startup to build the source list; if it is wrong the API falls back to
   hardcoded defaults and may report the wrong cameras.

EXECUTION CONTEXT
-----------------
These tests run inside the test_runner container with:
- /var/run/docker.sock mounted -> full access to the host Docker daemon,
  equivalent to running docker commands on the host.
- network_mode: host -> the TCP connection test to port 80 hits the real host.

PRECONDITIONS
-------------
- docker compose up -d must have been run before the test suite.
- Layer 1 tests should pass first (physical devices must exist on the host
  before they can be mapped into containers).
"""

import json
import socket

import pytest

from tests.utils.docker_helpers import (
    container_is_healthy,
    device_exists_in_container,
    get_container_env,
    image_exists,
)


@pytest.mark.layer2
def test_docker_images_exist():
    """
    Verify that the vid_mux and vid_mux_test Docker images exist in the local cache.

    If images are absent, docker compose up will attempt to pull them from a
    registry (and fail, since these are local builds) or refuse to start. This
    check produces a clear "build first" message instead of a cryptic container
    start failure.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If either required image is missing from the local cache.
    """
    for image in ("scanbox-vid_mux", "scanbox_vid_mux_test"):
        assert image_exists(image), (
            f"Docker image '{image}' not found locally — "
            f"run 'docker compose build' to build it"
        )


@pytest.mark.layer2
def test_physical_devices_in_vid_mux(api_url, http_session):
    """
    For every physical source reported by the API, verify its device node exists
    inside the vid_mux container.

    The device list is read dynamically from GET /api/v1/status rather than
    being hardcoded, so this test adapts automatically to single-camera and
    multi-camera setups configured via rebuild_vid_mux.sh. Only non-mock sources
    (device != /dev/video200) are checked.

    Args:
        api_url: Base API URL from the api_url session fixture.
        http_session: Shared requests.Session from the http_session fixture.

    Returns:
        None

    Raises:
        AssertionError: If any physical source device path is absent inside
            vid_mux, which would cause GStreamer to fail when switching to that
            source.
    """
    resp = http_session.get(f"{api_url}/api/v1/status")
    assert resp.status_code == 200
    sources = resp.json().get("sources", [])

    physical = [
        s for s in sources
        if s.get("device") and s["device"] != "/dev/video200"
    ]

    if not physical:
        pytest.skip("No physical sources reported by API — all synthetic")

    missing = []
    for src in physical:
        device = src["device"]
        if not device_exists_in_container("vid_mux", device):
            missing.append(f"src{src['id']} {device}")

    assert not missing, (
        f"Physical device(s) not found inside vid_mux container: {missing} — "
        "check the 'devices:' section in docker-compose.yml"
    )


@pytest.mark.layer2
def test_scanbox_dhcp_running():
    """
    Verify that the scanbox_dhcp container is running.

    The DHCP server assigns an IP address to the Windows PC over the USB NCM
    (network gadget) interface. Without it the PC cannot discover the
    Raspberry Pi and the SCANBOX UI is inaccessible over USB.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the container is stopped, crashed, or absent.
    """
    assert container_is_healthy("scanbox_dhcp"), (
        "Container 'scanbox_dhcp' is not running — USB NCM DHCP will not work"
    )


@pytest.mark.layer2
def test_vid_mux_test_running():
    """
    Verify that the vid_mux_test container is running.

    This container loads v4l2loopback and streams a synthetic SMPTE color-bar
    pattern to /dev/video200. It must stay running for the mock source to be
    available. If it exits, /dev/video200 disappears and GStreamer will fail
    when the pipeline tries to read from the mock source.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the container is stopped or absent.
    """
    assert container_is_healthy("vid_mux_test"), (
        "Container 'vid_mux_test' is not running — mock camera (/dev/video200) unavailable"
    )


@pytest.mark.layer2
def test_vid_mux_running():
    """
    Verify that the vid_mux container is running.

    vid_mux hosts the Flask REST API (port 80) and the GStreamer pipeline.
    All Layer 3, 4, and 5 tests depend on this container being alive and
    responsive. If it has crashed, those tests fail with connection errors
    rather than assertion failures.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the container is stopped or absent.
    """
    assert container_is_healthy("vid_mux"), (
        "Container 'vid_mux' is not running — Flask API and GStreamer pipeline unavailable"
    )


@pytest.mark.layer2
def test_port_80_listening():
    """
    Verify that TCP port 80 accepts connections on localhost.

    vid_mux binds Flask to 0.0.0.0:80. A successful TCP connect here means
    the process is alive and listening. A refused connection means Flask never
    started (GStreamer or import error at startup) even if the container shows
    as running in docker ps.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the connection to localhost:80 is refused or times out.
    """
    try:
        with socket.create_connection(("localhost", 80), timeout=3):
            pass
    except (ConnectionRefusedError, OSError) as exc:
        raise AssertionError(
            f"Port 80 is not accepting connections on localhost: {exc} — "
            "Flask may have failed to start inside vid_mux"
        )


@pytest.mark.layer2
def test_video100_in_container():
    """
    Verify that /dev/video100 exists inside the vid_mux container.

    /dev/video100 is the physical camera device mapped into vid_mux via
    the devices section in docker-compose.yml (using a stable by-id symlink
    on the host side). GStreamer opens this path to read frames from the
    physical USB camera.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the device path is absent inside the container.
    """
    assert device_exists_in_container("vid_mux", "/dev/video100"), (
        "/dev/video100 not found inside vid_mux — "
        "check the 'devices:' mapping in docker-compose.yml"
    )


@pytest.mark.layer2
def test_video200_in_container():
    """
    Verify that /dev/video200 exists inside the vid_mux container.

    /dev/video200 is the v4l2loopback mock camera device created by the
    vid_mux_test container. It is mapped into vid_mux via docker-compose
    devices so GStreamer can read the synthetic SMPTE stream from it.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the device path is absent inside the container.
    """
    assert device_exists_in_container("vid_mux", "/dev/video200"), (
        "/dev/video200 not found inside vid_mux — "
        "check the 'devices:' mapping in docker-compose.yml"
    )


@pytest.mark.layer2
def test_scanbox_sources_env_set():
    """
    Verify that SCANBOX_SOURCES is set inside vid_mux and is valid JSON with
    at least 2 entries.

    SCANBOX_SOURCES is a JSON array of source definitions injected via
    docker-compose environment. The Flask API parses it at startup to build
    the source list returned by GET /api/v1/status. Expected minimum: one
    physical camera (id=0, slot=/dev/video100) and one mock source (id=1,
    slot=/dev/video200). If missing or malformed, the API falls back to a
    hardcoded default that may not match the actual hardware.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If SCANBOX_SOURCES is unset, not valid JSON, or has
            fewer than 2 entries.
    """
    value = get_container_env("vid_mux", "SCANBOX_SOURCES")
    assert value is not None, (
        "SCANBOX_SOURCES env var is not set inside vid_mux — "
        "check the 'environment:' section in docker-compose.yml"
    )
    try:
        sources = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"SCANBOX_SOURCES is not valid JSON: {exc}\nValue: {value}"
        )
    assert len(sources) >= 2, (
        f"SCANBOX_SOURCES has only {len(sources)} entry(ies); "
        "expected at least 2 (1 physical + 1 mock)"
    )
