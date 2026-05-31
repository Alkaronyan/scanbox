# SCANBOX - Dynamic Video Switcher Module

A decoupled, deterministic video source switcher designed for embedded systems (Raspberry Pi CM4) running isolated within Docker containers. This module facilitates hot-swapping between physical hardware cameras and emulated testing streams via a REST API without breaking downstream video pipeline integrity.

## Project Architecture

### Containerization Philosophy (Strict)

Everything that *can* run inside a container *does* — both the production application and the test scaffold. The host OS is kept as clean as possible. Only the minimum that **must** live on the host kernel/OS is installed on it, and that minimum is fully automated by `setup_host.sh` and documented below. A fresh Raspberry Pi with nothing preinstalled must work after running that single script.

**Mandatory host-only requirements** (installed by `setup_host.sh`):
* **Docker Engine** — the container runtime/platform.
* **Kernel headers** (`linux-headers-*`) — required so the test container can compile `v4l2loopback` against the running host kernel and insert it into the shared kernel (the headers are mounted read-only into the container).
* **git** — to clone/update this repository on the host.

Everything else (GStreamer, Python, `v4l-utils`, build toolchain, `v4l2loopback` sources, etc.) lives **inside the containers** and must never be installed on the host.

---

The system enforces strict sandboxing and separation of concerns by splitting the application logic from the testing infrastructure:

* **Vid_Mux (Application Container):** The core production software. It ingests video inputs from static endpoints (/dev/video100 and /dev/video200), executes the GStreamer input-selector hot-swapping logic, handles snapshot actions, and exposes a JSON REST API on port 5000 for asynchronous signaling.
* **Vid_Mux_TEST (Mocking Container):** The development scaffold. It dynamically injects the v4l2loopback driver into the shared kernel to instantiate a persistent high-index virtual node (/dev/video200) and feeds it a synthetic SMPTE test pattern with timestamp metadata.

## Repository Structure

* app/Vid_Mux/ -> Production code, GStreamer engine, and Control API.
* test/Vid_Mux_TEST/ -> Virtual camera loopback scaffold (Development only).
* docs/ARCH_VID_MUX.md -> Detailed module behavior and contract.
* docs/ARCH_VID_MUX_TEST_FRAMEWORK.md -> Testing schematic and execution metrics.
* docs/RESTART_PROMPT.md -> Context template to resume LLM collaboration.

## Quick Start

### 1. Requirements
* Raspberry Pi running Raspberry Pi OS / Debian (aarch64). **Nothing else preinstalled is assumed.**
* VS Code with the Remote - SSH extension (for development).
* A physical USB Webcam connected to the host.

### 2. Host Provisioning (run once on a fresh Pi)
Install the mandatory host-only dependencies (Docker + kernel headers + git):
```bash
sudo ./setup_host.sh
```
Then log out and back in (or `newgrp docker`) so the docker group membership applies.

### 3. Run the Test Scaffold (creates /dev/video200)
```bash
cd test/Vid_Mux_TEST
docker build -t vid_mux_test .
# Resolve the host's kbuild scripts dir (version-agnostic) and mount all three paths
KBUILD_DIR="$(dirname "$(readlink -f /lib/modules/$(uname -r)/build/scripts)")"
docker run --rm --privileged --network=host \
  -v /lib/modules:/lib/modules:ro \
  -v /usr/src:/usr/src:ro \
  -v "${KBUILD_DIR}:${KBUILD_DIR}:ro" \
  vid_mux_test
```
> Three host paths must be mounted to compile `v4l2loopback`, because of the kernel header symlink chain:
> * `/lib/modules/<rel>/build` → `/usr/src/linux-headers-<rel>`
> * `/usr/src/linux-headers-*/scripts` → `/usr/lib/linux-kbuild-<ver>/scripts`
>
> So `/lib/modules`, `/usr/src`, **and** the `linux-kbuild` dir (resolved into `KBUILD_DIR`) are all required.

### 4. Synchronization
To verify and upload structural modifications to the remote repository, execute the standard Git workflow in your terminal:
* git add .
* git commit -m "chore: project infrastructure initialized"
* git push origin main
