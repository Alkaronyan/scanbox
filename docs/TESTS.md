# SCANBOX — Test Suites Reference

All test scripts live in `tests/`. Each is a self-contained bash script that prints `PASS` / `FAIL` per check and exits 0 (all pass) or 1 (any fail).

**Requirements for all suites:** the full stack must be running — either started manually via `./scripts/rebuild_vid_mux.sh` or automatically at boot via `scanbox.service`. Physical cameras must be connected.

**Env var:** `API_BASE` overrides the default `http://localhost` for all suites.
```bash
API_BASE=http://192.168.55.1 ./tests/run_all.sh   # test over USB NCM link
```

---

## tests/run_all.sh

**Purpose:** Master runner — executes all three suites in order and prints a summary table.

```bash
./tests/run_all.sh
```

Output:
```
┌─────────────────────┬────────┐
│ Suite               │ Result │
├─────────────────────┼────────┤
│ test_cameras.sh     │  PASS  │
│ test_api.sh         │  PASS  │
│ test_containers.sh  │  PASS  │
└─────────────────────┴────────┘
```

Exits 0 only if all three suites pass. Individual suite failures do not abort the run — all suites always execute.

---

## tests/test_cameras.sh

**Purpose:** End-to-end per-source test: switch → snapshot → verify file size.

Source list is read dynamically from `GET /api/v1/status` — no hardcoded IDs or counts.

**Checks per source:**
1. **Device node** — verifies the device path (e.g. `/dev/video100`) exists inside the `vid_mux` container. Skipped for mock/synthetic sources (`/dev/video200` or no slot) since vid_mux uses `videotestsrc` internally and does not map that device.
2. **Switch** — `POST /api/v1/source {"source_id": N}` returns HTTP 200
3. **Snapshot** — `POST /api/v1/snapshot` returns `status: success`
4. **File size** — snapshot JPEG exists on host in `snapshots/` and is > 5 KB (a blank or failed frame is typically < 1 KB)

After all sources are tested, restores the pipeline to source 0.

```bash
./tests/test_cameras.sh
```

---

## tests/test_api.sh

**Purpose:** Validates all REST API endpoints of the vid_mux Flask server.

Source list is read dynamically from `GET /api/v1/status`.

**Checks:**
1. `GET /api/v1/status` — HTTP 200, response contains `active_source` and `sources`
2. Source discovery — parses and logs all source IDs from the status response
3. Source count — verifies at least one source is available
4. `POST /api/v1/source` — cycles through all source IDs, each returns HTTP 200
5. `POST /api/v1/source` with `source_id: 99` — verifies rejection (non-200 or error body)
6. `POST /api/v1/snapshot` — HTTP 200, snapshot file appears in `snapshots/`
7. `GET /api/v1/snapshot/last` — HTTP 200, `Content-Type: image/jpeg`
8. `GET /api/v1/camera/controls` — HTTP 200, response contains `definitions` and `controls` fields. If `definitions` is non-empty, verifies each definition has `name`, `label`, `type`. Empty definitions are valid when the active source is mock/synthetic.
9. `POST /api/v1/camera/control` — sets `saturation=100`, verifies HTTP 200, then restores the original value. Skipped gracefully if the active source has no controls.
10. `GET /stream` — HTTP 200, `Content-Type: multipart/x-mixed-replace`

```bash
./tests/test_api.sh
API_BASE=http://192.168.55.1 ./tests/test_api.sh
```

---

## tests/test_containers.sh

**Purpose:** Verifies the Docker stack infrastructure is healthy before running functional tests.

**Checks:**
1. **Docker images** — `vid_mux` and `vid_mux_test` images exist
2. **Running containers** — `scanbox_dhcp`, `vid_mux_test`, and `vid_mux` are all running
3. **Port 80** — Flask is listening (checked via `ss` or HTTP probe)
4. **Device mappings** — for each physical source reported by `GET /api/v1/status`, verifies the device path is accessible inside the `vid_mux` container via `docker exec`. Mock/synthetic sources (no device or `/dev/video200`) are skipped.

```bash
./tests/test_containers.sh
```

This suite is designed to be run first (before test_cameras and test_api) to catch infrastructure problems early.
