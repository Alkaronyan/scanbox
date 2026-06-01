"""
image_analysis.py — JPEG frame analysis for SCANBOX behavioral tests.

PURPOSE
-------
Provides pixel-level analysis of JPEG snapshots captured via the SCANBOX API,
used by Layer 5 (behavioral) tests to verify that camera controls (brightness,
saturation) and source switches produce measurable, visually distinct frames.

All analysis is done by spawning `ffmpeg` as a subprocess and parsing its
signalstats filter output. No Python image libraries (PIL, OpenCV, numpy) are
required — ffmpeg is already present in the test_runner container.

HOW EACH METRIC WORKS
----------------------
- Mean brightness (YAVG): ffmpeg decodes the JPEG, applies the signalstats
  filter, and reports the mean luma (Y) value across all pixels. Range 0–255.
  A darker image has a lower YAVG.

- Color saturation (UAVG deviation): ffmpeg converts to YUV444 and reports the
  mean U chroma value. In a neutral/gray image U≈128; color shifts U away from
  128. The returned value is |UAVG - 128| * 2, so a fully gray frame returns
  0 and a strongly colored frame returns a positive value up to ~255.

- Frame difference (blend): ffmpeg scales both JPEGs to a common resolution
  (640x480) then computes the per-pixel absolute difference via the `blend`
  filter and reports the mean difference as YAVG. If YAVG > threshold*255,
  the frames are considered visually distinct.

DESIGN CONSTRAINTS
------------------
- No HTTP requests, no Docker calls — pure local file analysis.
- ffmpeg must be installed in the environment running these tests.
- All functions include a fallback raw-pixel path for robustness if
  ffmpeg's signalstats output format changes.

DEPENDENCIES
------------
- ffmpeg CLI with signalstats and blend filter support (standard build).
- JPEG files must exist at the paths provided; no path validation is performed.
"""

import os
import subprocess


def jpeg_filesize(path: str) -> int:
    """
    Return the size in bytes of a JPEG file.

    Used as a quick sanity check: a real camera frame is always larger than
    5 KB. A near-zero file indicates a blank, corrupt, or placeholder frame,
    which would cause all other analysis functions to return meaningless values.

    Args:
        path: Absolute path to a JPEG file on disk.

    Returns:
        File size in bytes. Values above 5000 indicate a real image.
    """
    return os.path.getsize(path)


def jpeg_mean_brightness(path: str) -> float:
    """
    Return the mean pixel brightness (luma) of a JPEG, in the range 0-255.

    Used by Layer 5 to verify that the brightness camera control has a
    measurable effect on the captured frame. A frame taken at brightness=220
    should have a significantly higher YAVG than one taken at brightness=30.

    Implementation: ffmpeg decodes the JPEG, applies the signalstats filter
    on the raw video stream, and reports YAVG (mean luma per frame) to stderr.
    Two fallback paths are tried if YAVG is absent from the primary output:
    1. format=gray,signalstats — forces grayscale conversion first.
    2. Raw gray pixel pipe — manually averages pixel bytes.

    Args:
        path: Absolute path to a JPEG file on disk.

    Returns:
        Float mean luma value in 0-255. Higher means brighter.

    Raises:
        AssertionError: If all three ffmpeg methods fail to produce output.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-i", path,
            "-vf", "signalstats",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    output = result.stderr
    for line in output.splitlines():
        if "YAVG" in line:
            parts = line.split("YAVG:")
            if len(parts) >= 2:
                try:
                    return float(parts[1].strip().split()[0])
                except ValueError:
                    pass

    # Fallback 1: explicit grayscale conversion before signalstats
    result2 = subprocess.run(
        [
            "ffmpeg", "-i", path,
            "-vf", "format=gray,signalstats=stat=tout+vrep+brng",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in result2.stderr.splitlines():
        if "YAVG" in line:
            parts = line.split("YAVG:")
            if len(parts) >= 2:
                try:
                    return float(parts[1].strip().split()[0])
                except ValueError:
                    pass

    # Fallback 2: decode to raw gray bytes and average manually
    result3 = subprocess.run(
        [
            "ffmpeg", "-i", path,
            "-vf", "scale=64:64",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ],
        capture_output=True,
    )
    raw = result3.stdout
    if not raw:
        raise AssertionError(f"ffmpeg produced no output for {path}")
    return sum(raw) / len(raw)


def jpeg_color_saturation(path: str) -> float:
    """
    Return a saturation score for a JPEG, in the approximate range 0-255.

    Used by Layer 5 to verify that the saturation camera control reduces color
    content. A frame at saturation=255 should score higher than one at
    saturation=0 (which produces a near-grayscale image with score ~0).

    Score formula: |UAVG - 128| * 2, where UAVG is the mean U chroma channel
    value from ffmpeg signalstats. In YUV color space, U=128 means no
    blue-yellow chroma shift (neutral gray). Deviation from 128 indicates
    color content. The factor of 2 maps the +/-128 deviation range to 0-255.

    Note: the score depends on scene content. A near-gray scene will produce
    a low score even at saturation=255. The test threshold (3 units) is
    intentionally low to accommodate low-color scenes while still confirming
    the control has any measurable effect.

    Implementation: primary path uses format=yuv444p,signalstats to force
    full chroma resolution before measurement. Fallback uses raw YUV420p pixels.

    Args:
        path: Absolute path to a JPEG file on disk.

    Returns:
        Float saturation score >= 0. Higher means more color; 0 means grayscale.

    Raises:
        AssertionError: If ffmpeg fails to produce any parseable output.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-i", path,
            "-vf", "format=yuv444p,signalstats",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in result.stderr.splitlines():
        if "UAVG" in line:
            parts = line.split("UAVG:")
            if len(parts) >= 2:
                try:
                    u_val = float(parts[1].strip().split()[0])
                    return abs(u_val - 128.0) * 2
                except ValueError:
                    pass

    # Fallback: decode to raw YUV420p and compute chroma mean manually.
    # YUV420p layout for a 64x64 frame: Y=4096 bytes, U=1024, V=1024 (total 6144).
    result2 = subprocess.run(
        [
            "ffmpeg", "-i", path,
            "-vf", "scale=64:64",
            "-f", "rawvideo", "-pix_fmt", "yuv420p", "pipe:1",
        ],
        capture_output=True,
    )
    raw = result2.stdout
    if not raw:
        raise AssertionError(f"ffmpeg produced no YUV output for {path}")
    total = len(raw)
    y_size = (total * 2) // 3
    uv = raw[y_size:]
    uv_mean = sum(uv) / len(uv) if uv else 128.0
    return abs(uv_mean - 128.0) * 2


def frames_are_different(path_a: str, path_b: str, threshold: float = 0.05) -> bool:
    """
    Return True if two JPEG frames differ visually by more than a threshold.

    Used by Layer 3 (pipeline) and Layer 5 (behavioral) tests to confirm that
    switching sources or changing controls actually changes the captured image.
    A return value of False means the pipeline may be stuck or the snapshot
    endpoint is serving a cached/stale frame.

    Implementation: ffmpeg scales both inputs to 640x480 (required because
    physical cameras and the mock source may produce different resolutions),
    then applies the blend=all_mode=difference filter to compute per-pixel
    absolute differences, and reads the mean difference (YAVG) from signalstats.
    If YAVG > threshold*255 the frames are considered distinct.

    The 640x480 normalization is mandatory: the blend filter requires identical
    dimensions, and physical cameras often produce 1920x1080 while the mock
    source produces 640x480.

    Fallback: if signalstats output is unavailable, both frames are decoded to
    raw 64x64 grayscale and their pixel arrays are compared manually.

    Args:
        path_a: Absolute path to the first JPEG file.
        path_b: Absolute path to the second JPEG file.
        threshold: Fraction of 255 that counts as a meaningful difference.
                   Default 0.05 (~13 out of 255 pixel units mean difference).

    Returns:
        True if the mean pixel difference exceeds threshold*255.
        False if the frames appear identical within the threshold.

    Raises:
        AssertionError: If ffmpeg cannot decode either file.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", path_a,
            "-i", path_b,
            "-filter_complex",
            # Both inputs scaled to same resolution before blending —
            # the blend filter requires identical dimensions.
            "[0:v]scale=640:480[a];[1:v]scale=640:480[b];[a][b]blend=all_mode=difference,signalstats",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in result.stderr.splitlines():
        if "YAVG" in line:
            parts = line.split("YAVG:")
            if len(parts) >= 2:
                try:
                    diff = float(parts[1].strip().split()[0])
                    return diff > (threshold * 255)
                except ValueError:
                    pass

    # Fallback: decode both to raw grayscale and compare pixel arrays manually.
    def decode_gray(p):
        r = subprocess.run(
            ["ffmpeg", "-i", p, "-vf", "scale=64:64",
             "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            capture_output=True,
        )
        return r.stdout

    raw_a = decode_gray(path_a)
    raw_b = decode_gray(path_b)
    if not raw_a or not raw_b:
        raise AssertionError(f"Could not decode frames for comparison: {path_a}, {path_b}")
    n = min(len(raw_a), len(raw_b))
    mean_diff = sum(abs(raw_a[i] - raw_b[i]) for i in range(n)) / n
    return mean_diff > (threshold * 255)
