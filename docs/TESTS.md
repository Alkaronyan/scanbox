# SCANBOX — Test Suite Reference

The SCANBOX test suite is a 5-layer pytest suite that runs entirely inside the
`test_runner` Docker container. No tools need to be installed on the host — the
container already has pytest, ffmpeg, requests, and the Docker CLI.

---

## Quick Start

```bash
# Interactive menu (recommended for manual runs)
./run_tests.sh

# Run all layers directly
./run_tests.sh                                      # menu
docker compose --profile test run --rm --no-deps test_runner

# Run a single layer
./run_tests.sh -m layer3
docker compose --profile test run --rm --no-deps test_runner -m layer4

# Run a single test
./run_tests.sh tests/layer5_behavior/test_behavior.py::test_mock_source_differs_from_physical

# List all tests without running them
./run_tests.sh --co -q
```

**Requirements:** the full stack must already be running (`docker compose up -d`).
The `test_runner` container uses the same image as `vid_mux_test` — no separate
build step is needed.

---

## Architecture

### Why tests run inside a container

All tests run inside the `test_runner` Docker container, defined in
`docker-compose.yml` under the `test` profile. The project directory
`/home/Alfred/scanbox` is **bind-mounted** at the same path inside the container,
so snapshot file paths resolve identically inside and outside the container — no
file copying needed at build time.

```
Host                              test_runner container
────────────────────────────────────────────────────────
/home/Alfred/scanbox  ──mount──▶  /home/Alfred/scanbox
/var/run/docker.sock  ──mount──▶  /var/run/docker.sock
/dev/v4l              ──mount──▶  /dev/v4l  (Layer 1 symlinks)
network_mode: host               reaches Flask API at localhost:80
privileged: true                 /dev/* visible, lsmod works
```

This design allows:
- **Layer 1** (hardware): `lsmod` reads `/proc/modules` from the real host kernel
  because Linux containers share the kernel. `privileged: true` removes the device
  cgroup whitelist so `/dev/video*` nodes are visible.
- **Layer 2** (containers): the Docker socket gives the `docker` CLI inside the
  container full access to the host daemon.
- **Layers 3–5** (pipeline, API, behavior): `network_mode: host` reaches the
  Flask API on `localhost:80`.

### File locations

```
Vid_Mux_TEST/
└── tests/
    ├── pytest.ini                   # markers, testpaths, addopts
    ├── conftest.py                  # shared fixtures (api_url, sources, snapshot_collector …)
    ├── utils/
    │   ├── docker_helpers.py        # docker CLI wrappers (image_exists, container_is_healthy …)
    │   ├── image_analysis.py        # ffmpeg-based JPEG analysis (brightness, saturation, diff)
    │   └── snapshot_helpers.py      # deterministic snapshot filename generator
    ├── layer1_host/
    │   └── test_hardware.py
    ├── layer2_containers/
    │   └── test_containers.py
    ├── layer3_pipeline/
    │   └── test_pipeline.py
    ├── layer4_api/
    │   └── test_api.py
    └── layer5_behavior/
        └── test_behavior.py
```

> **Historical note:** the project previously had bash test scripts
> (`tests/test_cameras.sh`, `test_api.sh`, `test_containers.sh`, `run_all.sh`).
> These have been superseded by this pytest suite and deleted.

---

## Snapshot naming and cleanup

Tests that capture snapshots use the `snapshot_collector` fixture and the
`snap_name()` helper to generate meaningful filenames instead of the API's
default timestamp-based names.

**Naming pattern:** `{test_name}__{key}{value}[__{…}].jpg`

Examples:
```
test_each_source_produces_snapshot__src0.jpg
test_switching_produces_different_frames__src0__framea.jpg
test_saturation_zero__src0__sat255.jpg
test_brightness_change__src0__bri30.jpg
test_snapshot_reflects__src1__seq2.jpg
```

**Cleanup behaviour:** snapshots created by a passing test are **deleted
automatically**. Snapshots from a failing test are left in `./snapshots/` for
visual inspection. Running the test again after fixing the failure cleans them up.

The API's `POST /api/v1/snapshot` accepts an optional `filename` field in the
request body:
```json
{"filename": "my_descriptive_name.jpg"}
```

---

## Layer 1 — Hardware & host OS

**File:** `Vid_Mux_TEST/tests/layer1_host/test_hardware.py`
**Marker:** `-m layer1`

These are the lowest-level checks. If any fail, all upper layers will also fail.

| Test | What it checks | Failure means |
|---|---|---|
| `test_v4l2loopback_loaded` | `v4l2loopback` in `lsmod` output | Module not loaded; `/dev/video200` cannot exist |
| `test_mock_device_exists` | `/dev/video200` exists on host | v4l2loopback not loaded with `video_nr=200` |
| `test_at_least_one_physical_camera` | `by-id/*-video-index0` symlinks exist | No physical camera connected |
| `test_physical_camera_devices_readable` | Each camera device node is readable | Permission problem; GStreamer cannot open device |

---

## Layer 2 — Container health

**File:** `Vid_Mux_TEST/tests/layer2_containers/test_containers.py`
**Marker:** `-m layer2`

Verifies the full Docker stack is built, running, and correctly configured.

| Test | What it checks | Failure means |
|---|---|---|
| `test_docker_images_exist` | `scanbox-vid_mux` and `scanbox_vid_mux_test` images in local cache | `docker compose build` has not been run |
| `test_physical_devices_in_vid_mux` | Every physical source device (from API) exists inside vid_mux | Wrong `devices:` mapping in docker-compose.yml |
| `test_scanbox_dhcp_running` | `scanbox_dhcp` container is running | USB NCM DHCP unavailable |
| `test_vid_mux_test_running` | `vid_mux_test` container is running | Mock camera unavailable |
| `test_vid_mux_running` | `vid_mux` container is running | Flask API and pipeline unavailable |
| `test_port_80_listening` | TCP connect to `localhost:80` succeeds | Flask failed to start inside vid_mux |
| `test_video100_in_container` | `/dev/video100` exists inside vid_mux | Physical camera not mapped into container |
| `test_video200_in_container` | `/dev/video200` exists inside vid_mux | Mock device not mapped into container |
| `test_scanbox_sources_env_set` | `SCANBOX_SOURCES` is set, valid JSON, ≥2 entries | API will use wrong hardcoded source list |

---

## Layer 3 — GStreamer pipeline

**File:** `Vid_Mux_TEST/tests/layer3_pipeline/test_pipeline.py`
**Marker:** `-m layer3`

Verifies the GStreamer pipeline produces real frames and switches correctly.
Uses the snapshot API endpoint as the frame extraction mechanism.

| Test | What it checks | Failure means |
|---|---|---|
| `test_each_source_produces_snapshot` | Each source produces a JPEG > 5 KB | GStreamer blank/error frame for that source |
| `test_switching_produces_different_frames` | Frames from source 0 and last source are visually distinct | Pipeline not switching; stale frame returned |
| `test_stream_endpoint_delivers_frames` | `GET /stream` delivers ≥50 KB with `--frame` boundary markers | MJPEG streaming broken |

**Key design note — appsink backpressure:** GStreamer's appsink stops calling
`_on_new_sample` when its buffer is full and no consumer is draining it. The
`vid_mux` application runs a permanent background thread (`frame_refresher`) that
continuously drains the queue so the pipeline never stalls and `_last_frame` is
always current.

---

## Layer 4 — API contract

**File:** `Vid_Mux_TEST/tests/layer4_api/test_api.py`
**Marker:** `-m layer4`

Verifies every public endpoint returns the correct HTTP status and response structure.

| Test | Endpoint | What it checks |
|---|---|---|
| `test_status_returns_ok` | `GET /api/v1/status` | HTTP 200 |
| `test_status_has_sources_list` | `GET /api/v1/status` | `sources` key present |
| `test_status_sources_not_empty` | `GET /api/v1/status` | Sources list has ≥1 entry |
| `test_status_active_source_in_sources` | `GET /api/v1/status` | `active_source` is a valid source ID |
| `test_switch_to_each_source` | `POST /api/v1/source` | HTTP 200 + `status=success` for every source |
| `test_switch_invalid_source_returns_error` | `POST /api/v1/source` | HTTP 400 for `source_id=999` |
| `test_snapshot_returns_success` | `POST /api/v1/snapshot` | HTTP 200 + `status=success` |
| `test_snapshot_file_created` | `POST /api/v1/snapshot` | File exists on disk at returned path |
| `test_snapshot_last_returns_jpeg` | `GET /api/v1/snapshot/last` | HTTP 200 + `Content-Type: image/jpeg` |
| `test_camera_controls_returns_definitions` | `GET /api/v1/camera/controls` | `definitions` and `controls` keys present |
| `test_camera_controls_definition_structure` | `GET /api/v1/camera/controls` | Each definition has `name`, `label`, `type` |
| `test_camera_control_set_and_restore` | `POST /api/v1/camera/control` | Set saturation + restore round-trip succeeds |
| `test_stream_returns_multipart` | `GET /stream` | `Content-Type: multipart/x-mixed-replace` |

---

## Layer 5 — Behavioral / visual

**File:** `Vid_Mux_TEST/tests/layer5_behavior/test_behavior.py`
**Marker:** `-m layer5`

Verifies that camera controls and source switches produce measurable visual effects.
Tests 1 and 2 require a physical camera with V4L2 controls — they skip automatically
when only the mock source is available.

| Test | What it checks | Threshold |
|---|---|---|
| `test_saturation_zero_produces_grayscale` | `sat=255` frame scores >3 units higher than `sat=0` frame | 3 saturation units (scene-dependent) |
| `test_brightness_change_affects_luminance` | `bri=220` frame has >30 luma units more than `bri=30` frame | 30 luma units |
| `test_mock_source_differs_from_physical` | SMPTE bars vs physical camera are visually distinct | Default 5% pixel difference |
| `test_snapshot_reflects_active_source` | Every consecutive source-switch pair produces distinct frames | Default 5% pixel difference |

**Saturation threshold is 3 (not 20):** the saturation score (`|UAVG − 128| × 2`)
is scene-dependent. A near-gray scene produces a low score even at `sat=255`. The
threshold confirms the control has any measurable effect, not that the scene is colorful.

---

## Shared fixtures (conftest.py)

| Fixture | Scope | Purpose |
|---|---|---|
| `api_url` | session | Base URL from `SCANBOX_API_URL` env var (default `http://localhost:80`) |
| `http_session` | session | `requests.Session` with 5-second timeout |
| `snapshots_dir` | session | `/home/Alfred/scanbox/snapshots` |
| `sources` | session | Source list from `GET /api/v1/status`, fetched once |
| `snapshot_collector` | function | Collects snapshot paths; deletes on pass, keeps on failure |
| `active_source_restored` | function | Saves active source before test, restores it after |

---

## Utilities (tests/utils/)

### docker_helpers.py
Thin subprocess wrappers for Docker CLI calls. Used by Layer 2 tests.

- `image_exists(name)` — checks local image cache
- `container_is_healthy(name)` — checks `State.Running` via inspect
- `exec_in_container(container, cmd)` — runs a read-only command inside a container
- `device_exists_in_container(container, device)` — checks device path via `test -e`
- `get_container_env(container, var)` — reads an env var via `printenv`
- `get_running_containers()` — returns list of running container names

### image_analysis.py
ffmpeg-based JPEG analysis. Used by Layer 3 and Layer 5 tests.

- `jpeg_filesize(path)` — file size in bytes (quick sanity check)
- `jpeg_mean_brightness(path)` — mean luma (YAVG) via signalstats, range 0–255
- `jpeg_color_saturation(path)` — `|UAVG − 128| × 2` via YUV444 signalstats, range 0–255
- `frames_are_different(path_a, path_b, threshold=0.05)` — ffmpeg blend filter with
  640×480 normalization; True if mean pixel diff > threshold×255

### snapshot_helpers.py
- `snap_name(test_name, **tags)` — builds a safe filename encoding test identity and
  parameter values, e.g. `test_foo__src0__sat255.jpg`

---

## pytest.ini

```ini
[pytest]
markers =
    layer1: Hardware and host OS
    layer2: Container health
    layer3: GStreamer pipeline
    layer4: API contract
    layer5: Behavioral / visual
testpaths = tests
addopts = -v --tb=short
```

Located at `Vid_Mux_TEST/tests/pytest.ini`. The `test_runner` container's
`working_dir` is `/home/Alfred/scanbox/Vid_Mux_TEST`, so `testpaths = tests`
resolves to `Vid_Mux_TEST/tests/`.
