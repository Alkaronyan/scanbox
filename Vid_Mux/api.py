#!/usr/bin/env python3
# api.py — REST API + Web UI + MJPEG stream server for Vid_Mux.
#
# Endpoints:
#   GET  /                          — Web UI
#   GET  /stream                    — Live MJPEG stream (browser-native)
#   GET  /api/v1/status             — Current active source (JSON)
#   POST /api/v1/source             — Switch active source (JSON)
#   POST /api/v1/snapshot           — Capture JPEG snapshot (JSON)
#   GET  /api/v1/snapshot/last      — Serve last snapshot image
#   GET  /api/v1/camera/controls    — Get current V4L2 control values
#   POST /api/v1/camera/control     — Set a V4L2 control value

import os
import json
import glob
import datetime
import logging
import queue
import subprocess
import re
import threading
from flask import Flask, request, jsonify, send_file, render_template, Response
import switcher

log = logging.getLogger(__name__)

app = Flask(__name__)

SNAPSHOT_DIR = "/exports/snapshots"
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# Last frame cache — written by the MJPEG stream generator, read by snapshot.
# Prevents snapshot from competing with an active stream connection for queue items.
_last_frame: bytes | None = None
_last_frame_lock = threading.Lock()

def _active_physical_device() -> str | None:
    """Return the device path of the currently active source, or None if it has no real V4L2
    controls (mock camera on /dev/video200, SMPTE fallback with no slot)."""
    active_id = switcher.get_active_source()
    source = next((s for s in SOURCES if s["id"] == active_id), None)
    if source is None:
        return None
    device = source.get("device", "")
    if not device or device == "/dev/video200":
        return None
    return device


def _make_display_name(label: str) -> str:
    """Derive a human-readable display name from a source label."""
    if label == "mock":
        return "Mock Camera"
    # Physical cameras carry their by-id label, e.g. "usb-046d_0809_5DD0F8C2"
    # Strip common prefixes to make them more readable in the UI.
    name = label.replace("usb-", "").replace("_", " ").strip()
    return name if name else label


def _build_sources_list() -> list[dict]:
    """
    Build the SOURCES list for the API from the SCANBOX_SOURCES env var.
    Falls back to the hardcoded [video100, video200] pair if env var is absent.
    Returns a list of dicts with keys: id, name, device.
    """
    raw = os.environ.get("SCANBOX_SOURCES", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                result = []
                for entry in parsed:
                    src_id = int(entry["id"])
                    slot   = entry.get("slot", "")
                    label  = entry.get("label", "")
                    result.append({
                        "id":     src_id,
                        "name":   _make_display_name(label),
                        "device": slot,
                    })
                log.info("API: loaded %d source(s) from SCANBOX_SOURCES.", len(result))
                return result
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.warning("API: SCANBOX_SOURCES parse error (%s) — using hardcoded fallback.", e)

    # Hardcoded fallback (matches original behaviour)
    log.warning("API: SCANBOX_SOURCES not set — using default [video100, video200].")
    return [
        {"id": 0, "name": "Physical Camera", "device": "/dev/video100"},
        {"id": 1, "name": "Mock Camera",     "device": "/dev/video200"},
    ]


# Camera source definitions — built dynamically from SCANBOX_SOURCES at startup.
SOURCES: list[dict] = _build_sources_list()

# ---------------------------------------------------------------------------
# V4L2 dynamic control detection
# ---------------------------------------------------------------------------

# Human-readable labels for known V4L2 control names.
_CTRL_LABELS: dict[str, str] = {
    'brightness':                'Brightness',
    'contrast':                  'Contrast',
    'saturation':                'Saturation',
    'sharpness':                 'Sharpness',
    'gain':                      'Gain',
    'backlight_compensation':    'Backlight Comp.',
    'auto_exposure':             'Auto Exposure',
    'exposure_time_absolute':    'Exposure',
    'exposure_dynamic_framerate':'Dynamic FPS',
    'white_balance_automatic':   'Auto White Balance',
    'white_balance_temperature': 'White Balance',
    'power_line_frequency':      'Anti-Flicker',
    'pan_absolute':              'Pan',
    'tilt_absolute':             'Tilt',
    'zoom_absolute':             'Zoom',
    'focus_absolute':            'Focus',
    'focus_automatic_continuous':'Autofocus',
}


def _parse_v4l2_output(output: str) -> tuple[list[dict], dict[str, int | None]]:
    """
    Parse v4l2-ctl --list-ctrls-menus output.
    Returns:
      definitions : list of control dicts (name, label, type, min, max, step, default, inactive, options?)
      values      : {name: current_value_int_or_None}
    """
    definitions: list[dict] = []
    values: dict[str, int | None] = {}
    current_menu: dict | None = None

    for line in output.splitlines():
        # Menu option line: whitespace + integer + colon + label
        if current_menu is not None and re.match(r'^\s+\d+:', line):
            m = re.match(r'^\s+(-?\d+):\s+(.*)', line)
            if m:
                current_menu['options'][int(m.group(1))] = m.group(2).strip()
            continue
        else:
            current_menu = None

        # Control line: "  name 0xADDR (type) : key=val ..."
        m = re.match(
            r'^\s{1,30}(\w+)\s+0x[0-9a-f]+\s+\((int|bool|menu|button)\)\s+:(.+)',
            line
        )
        if not m:
            continue

        name     = m.group(1)
        raw_type = m.group(2)
        params   = m.group(3)

        kv       = {k: int(v) for k, v in re.findall(r'(?<!\w)(\w+)=(-?\d+)', params)}
        inactive = 'inactive' in params

        ctrl_type = ('bool' if raw_type == 'bool' else
                     'menu' if raw_type == 'menu' else 'int')

        ctrl: dict = {
            'name':     name,
            'label':    _CTRL_LABELS.get(name, name.replace('_', ' ').title()),
            'type':     ctrl_type,
            'min':      kv.get('min', 0),
            'max':      kv.get('max', 1),
            'step':     kv.get('step', 1),
            'default':  kv.get('default', 0),
            'inactive': inactive,
        }

        if ctrl_type == 'menu':
            ctrl['options'] = {}
            current_menu = ctrl

        definitions.append(ctrl)
        values[name] = kv.get('value')

    return definitions, values


def _query_camera_controls(device: str) -> tuple[list[dict], dict[str, int | None]]:
    """Run v4l2-ctl --list-ctrls-menus and return (definitions, values)."""
    try:
        r = subprocess.run(
            ['v4l2-ctl', '-d', device, '--list-ctrls-menus'],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            log.warning("v4l2-ctl failed for %s: %s", device, r.stderr.strip())
            return [], {}
        return _parse_v4l2_output(r.stdout)
    except Exception as e:
        log.warning("Could not query controls for %s: %s", device, e)
        return [], {}


def _set_ctrl(name: str, value: int) -> bool:
    device = _active_physical_device()
    if device is None:
        return False
    try:
        r = subprocess.run(
            ["v4l2-ctl", "-d", device, f"--set-ctrl={name}={value}"],
            capture_output=True, text=True, timeout=3
        )
        return r.returncode == 0
    except Exception as e:
        log.error("set_ctrl %s=%s failed: %s", name, value, e)
        return False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_snapshot() -> str | None:
    files = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "snap_*.jpg")))
    return files[-1] if files else None


def _save_snapshot(jpeg_bytes: bytes) -> str:
    ts = datetime.datetime.now().strftime("%Y_%m_%d__%H_%M_%S")
    path = os.path.join(SNAPSHOT_DIR, f"snap_{ts}.jpg")
    with open(path, "wb") as f:
        f.write(jpeg_bytes)
    return path

# ---------------------------------------------------------------------------
# MJPEG stream
# ---------------------------------------------------------------------------

def _mjpeg_generator():
    global _last_frame
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
        try:
            frame = switcher.frame_queue.get(timeout=2.0)
            with _last_frame_lock:
                _last_frame = frame
        except queue.Empty:
            # During source switches there is a brief gap with no new frames.
            # Send the last known frame (freeze) instead of an empty part —
            # empty multipart parts break browser MJPEG connections.
            with _last_frame_lock:
                frame = _last_frame
            if frame is None:
                continue
        yield boundary + frame + b"\r\n"

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/stream")
def stream():
    return Response(
        _mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/v1/status")
def status():
    active = switcher.get_active_source()
    source = next((s for s in SOURCES if s["id"] == active), None)
    return jsonify({
        "status": "ok",
        "active_source": active,
        "source_name": source["name"] if source else "unknown",
        "sources": SOURCES,
    })


@app.post("/api/v1/source")
def set_source():
    data = request.get_json(silent=True)
    if data is None or "source_id" not in data:
        return jsonify({"status": "error", "message": "Missing 'source_id'"}), 400
    source_id = data["source_id"]
    valid_ids = [s["id"] for s in SOURCES]
    if source_id not in valid_ids:
        return jsonify({"status": "error", "message": f"source_id must be one of {valid_ids}"}), 400
    success = switcher.switch_source(source_id)
    if not success:
        return jsonify({"status": "error", "message": "Pipeline not ready or switch failed"}), 503
    source = next(s for s in SOURCES if s["id"] == source_id)
    return jsonify({"status": "success", "active_source": source_id, "source_name": source["name"]})


@app.post("/api/v1/snapshot")
def snapshot():
    # Use the last frame cached by the stream generator (avoids competing with
    # an active MJPEG stream connection that drains the queue continuously).
    with _last_frame_lock:
        frame = _last_frame
    if frame is None:
        # No stream consumer active — pull directly from the queue.
        try:
            frame = switcher.frame_queue.get(timeout=3.0)
        except queue.Empty:
            return jsonify({"status": "error", "message": "No frame available"}), 504
    path = _save_snapshot(frame)
    log.info("Snapshot saved: %s", path)
    return jsonify({"status": "success", "file_path": path, "filename": os.path.basename(path)})


@app.get("/api/v1/snapshot/last")
def last_snapshot():
    path = _last_snapshot()
    if path is None:
        return jsonify({"status": "error", "message": "No snapshots yet"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.get("/api/v1/camera/controls")
def get_controls():
    """Return V4L2 controls for the currently active source. Empty if source has no controls."""
    device = _active_physical_device()
    if device is None:
        return jsonify({"status": "ok", "controls": {}, "definitions": [], "message": "No controls for this source"})
    defs, vals = _query_camera_controls(device)
    return jsonify({"status": "ok", "controls": vals, "definitions": defs})


@app.post("/api/v1/camera/control")
def set_control():
    """Set a single V4L2 control. Body: {"control": "saturation", "value": 128}"""
    data = request.get_json(silent=True)
    if not data or "control" not in data or "value" not in data:
        return jsonify({"status": "error", "message": "Missing 'control' or 'value'"}), 400
    name  = str(data["control"])
    value = int(data["value"])
    success = _set_ctrl(name, value)
    if not success:
        return jsonify({"status": "error", "message": f"Failed to set {name}={value}"}), 500
    return jsonify({"status": "success", "control": name, "value": value})


@app.get("/")
def ui():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='[api] %(asctime)s %(levelname)s: %(message)s'
    )
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)