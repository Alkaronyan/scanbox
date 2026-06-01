"""
docker_helpers.py — Docker CLI wrappers for the SCANBOX test suite.

PURPOSE
-------
Provides a thin, subprocess-based interface to the Docker daemon so that
Layer 2 (container health) and Layer 3 (pipeline) tests can inspect and
interact with running containers without importing the Docker SDK.

All functions call the `docker` CLI directly and return Python-native types.
They never raise exceptions on Docker errors — instead they return safe
defaults (False, None, empty list) so that individual tests can compose
assertions with clear messages.

DESIGN CONSTRAINTS
------------------
- No HTTP calls, no image analysis — pure Docker introspection.
- Works from inside the `test_runner` container because `/var/run/docker.sock`
  is bind-mounted, giving it access to the host Docker daemon.
- All `exec_in_container` calls are read-only (test -e, printenv) — no side effects.

DEPENDENCIES
------------
- `docker` CLI must be installed inside the test_runner container.
- The host Docker socket must be mounted at /var/run/docker.sock.
"""

import subprocess


def image_exists(name: str) -> bool:
    """
    Check whether a Docker image is present in the local image cache.

    Used by Layer 2 to verify that `docker compose build` has been run before
    the stack is started. If images are missing the containers cannot be
    created and all downstream tests will fail with connection errors rather
    than clear image-missing messages.

    Args:
        name: Image name or name:tag, e.g. "scanbox-vid_mux" or
              "scanbox_vid_mux_test:latest".

    Returns:
        True if `docker image inspect <name>` exits with code 0 (image found).
        False if the image is absent or the docker command fails.
    """
    result = subprocess.run(
        ["docker", "image", "inspect", name],
        capture_output=True,
    )
    return result.returncode == 0


def get_running_containers() -> list:
    """
    Return the names of all currently running Docker containers on the host.

    Used by Layer 2 to confirm that expected containers are alive. A container
    that has crashed or never started will be absent from this list.

    Returns:
        List of container name strings (e.g. ["vid_mux", "vid_mux_test"]).
        Returns an empty list if `docker ps` fails.
    """
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def container_is_healthy(name: str) -> bool:
    """
    Check whether a named container is currently in the running state.

    This checks `State.Running` via `docker inspect`, which is True only while
    the container process is alive. A container that has exited (even with
    exit code 0) or is paused returns False.

    Note: this does NOT check Docker healthcheck status — only running state.

    Args:
        name: Docker container name, e.g. "vid_mux" or "scanbox_dhcp".

    Returns:
        True if the container exists and State.Running is true.
        False if the container is stopped, missing, or inspect fails.
    """
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def exec_in_container(container: str, cmd: list) -> tuple:
    """
    Execute a command inside a running container and capture its output.

    Used to inspect container internals (e.g. check if a device file exists,
    read an environment variable) without opening a shell or modifying state.

    Args:
        container: Name of the running container, e.g. "vid_mux".
        cmd: Command and arguments as a list, e.g. ["test", "-e", "/dev/video100"].

    Returns:
        Tuple of (return_code: int, output: str) where output combines
        stdout and stderr. A return_code of 0 means success.
    """
    result = subprocess.run(
        ["docker", "exec", container] + cmd,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return result.returncode, output


def device_exists_in_container(container: str, device: str) -> bool:
    """
    Check whether a device node path exists inside a running container.

    Used by Layer 2 to confirm that `--device` mappings in docker-compose.yml
    are correctly applied. A physical camera mapped as /dev/video100 inside
    vid_mux must exist there for GStreamer to open it.

    Args:
        container: Name of the running container, e.g. "vid_mux".
        device: Device path to test inside the container, e.g. "/dev/video100".

    Returns:
        True if the path exists inside the container (`test -e` exits 0).
        False if the path is absent or the container is unreachable.
    """
    rc, _ = exec_in_container(container, ["test", "-e", device])
    return rc == 0


def get_container_env(container: str, var: str):
    """
    Read a single environment variable from a running container.

    Used by Layer 2 to verify that docker-compose `environment:` entries are
    correctly injected. For example, SCANBOX_SOURCES must be set and valid JSON
    for the API to serve the correct source list at startup.

    Args:
        container: Name of the running container, e.g. "vid_mux".
        var: Environment variable name, e.g. "SCANBOX_SOURCES".

    Returns:
        The string value of the variable if set and non-empty.
        None if the variable is unset, empty, or the container is missing.
    """
    result = subprocess.run(
        ["docker", "exec", container, "printenv", var],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if value else None
