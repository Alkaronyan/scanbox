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
import glob
import datetime
import logging
import queue
import subprocess
import re
from flask import Flask, request, jsonify, send_file, render_template, Response
import switcher

log = logging.getLogger(__name__)

app = Flask(__name__)

SNAPSHOT_DIR = "/exports/snapshots"
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

PHYSICAL_DEVICE = "/dev/video100"

# Camera source definitions — extend as new sources are added.
SOURCES = [
    {"id": 0, "name": "Physical Camera", "device": "/dev/video100"},
    {"id": 1, "name": "Mock Camera",     "device": "/dev/video200"},
]

# V4L2 controls exposed in the UI.
# type: int | bool | menu
# depends_on: control that must have a specific value to enable this one
CAMERA_CONTROLS = [
    {"name": "brightness",              "label": "Brightness",            "type": "int",  "min": 0,   "max": 255,   "default": 128},
    {"name": "contrast",                "label": "Contrast",              "type": "int",  "min": 0,   "max": 255,   "default": 32},
    {"name": "saturation",              "label": "Saturation",            "type": "int",  "min": 0,   "max": 255,   "default": 28},
    {"name": "sharpness",               "label": "Sharpness",             "type": "int",  "min": 0,   "max": 255,   "default": 191},
    {"name": "gain",                    "label": "Gain",                  "type": "int",  "min": 0,   "max": 255,   "default": 0},
    {"name": "backlight_compensation",  "label": "Backlight Compensation","type": "bool", "min": 0,   "max": 1,     "default": 1},
    {"name": "auto_exposure",           "label": "Auto Exposure",         "type": "menu",
     "options": {1: "Manual Mode", 3: "Aperture Priority (Auto)"}, "default": 3},
    {"name": "exposure_time_absolute",  "label": "Exposure",              "type": "int",  "min": 1,   "max": 10000, "default": 166,
     "depends_on": {"control": "auto_exposure", "value": 1}},
]

# ---------------------------------------------------------------------------
# V4L2 helpers
# ---------------------------------------------------------------------------

def _get_ctrl(name: str) -> int | None:
    try:
        r = subprocess.run(
            ["v4l2-ctl", "-d", PHYSICAL_DEVICE, f"--get-ctrl={name}"],
            capture_output=True, text=True, timeout=3
        )
        m = re.search(r":\s*(-?\d+)", r.stdout)
        return int(m.group(1)) if m else None
    except Exception as e:
        log.error("get_ctrl %s failed: %s", name, e)
        return None


def _set_ctrl(name: str, value: int) -> bool:
    try:
        r = subprocess.run(
            ["v4l2-ctl", "-d", PHYSICAL_DEVICE, f"--set-ctrl={name}={value}"],
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
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
        try:
            frame = switcher.frame_queue.get(timeout=2.0)
            yield boundary + frame + b"\r\n"
        except queue.Empty:
            yield b"--frame\r\n\r\n"

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
    """Return current values for all exposed V4L2 controls."""
    values = {}
    for ctrl in CAMERA_CONTROLS:
        val = _get_ctrl(ctrl["name"])
        values[ctrl["name"]] = val
    return jsonify({"status": "ok", "controls": values, "definitions": CAMERA_CONTROLS})


@app.post("/api/v1/camera/control")
def set_control():
    """Set a single V4L2 control. Body: {"control": "saturation", "value": 128}"""
    data = request.get_json(silent=True)
    if not data or "control" not in data or "value" not in data:
        return jsonify({"status": "error", "message": "Missing 'control' or 'value'"}), 400
    name = data["control"]
    value = int(data["value"])
    valid = [c["name"] for c in CAMERA_CONTROLS]
    if name not in valid:
        return jsonify({"status": "error", "message": f"Unknown control '{name}'"}), 400
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