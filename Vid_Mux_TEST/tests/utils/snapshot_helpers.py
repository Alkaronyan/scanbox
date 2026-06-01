"""
snapshot_helpers.py — Snapshot filename generation for the SCANBOX test suite.

PURPOSE
-------
Provides a single function that builds deterministic, human-readable JPEG
filenames for snapshots taken during tests. Meaningful filenames serve two
goals:

1. IDENTIFICATION — a filename like
   "test_saturation_zero__src0__sat255.jpg" immediately tells a developer
   which test created it, which camera was active, and what parameter was
   being tested — without opening the file.

2. COLLISION AVOIDANCE — the API's default timestamp-based naming
   (snap_YYYY_MM_DD__HH_MM_SS.jpg) has one-second resolution. Two snapshots
   taken within the same second would overwrite each other. Named snapshots
   have unique filenames regardless of timing.

NAMING CONVENTION
-----------------
Pattern:  {test_name}__{key1}{val1}__{key2}{val2}...jpg
Example:  test_brightness_change__src0__bri220.jpg
Example:  test_switching__src0__framea.jpg
Example:  test_snapshot_reflects__src1__seq2.jpg

The test name is stripped of any pytest module path prefix (everything before
the last "::") and non-alphanumeric characters are replaced with underscores.
Tags are concatenated key+value with no separator (e.g. src=0 → "src0").

CLEANUP BEHAVIOUR
-----------------
Snapshot files are registered via the `snapshot_collector` fixture defined in
conftest.py. That fixture deletes all registered paths if the test passes, and
leaves them on disk if the test fails — so failed-test images are available
for post-mortem inspection.

DEPENDENCIES
------------
- Standard library only (re). No HTTP calls, no Docker, no image analysis.
"""

import re


def snap_name(test_name: str, **tags) -> str:
    """
    Build a deterministic snapshot filename from a test name and keyword tags.

    The resulting filename uniquely identifies the test, the camera source,
    and any relevant parameter values. It is passed as the `filename` field
    in the POST /api/v1/snapshot request body so the API saves the file under
    this name instead of its default timestamp-based name.

    Args:
        test_name: The pytest node name for the current test, typically obtained
                   via `request.node.name`. Module path prefixes separated by
                   "::" are stripped. Non-alphanumeric characters are replaced
                   with underscores.
        **tags:    Arbitrary key=value pairs appended in definition order.
                   Each tag is rendered as the key string immediately followed
                   by the value string (e.g. src=0 -> "src0", bri=220 -> "bri220").

    Returns:
        A safe filename string ending in ".jpg", e.g.
        "test_saturation_zero__src0__sat255.jpg".

    Example:
        snap_name("test_saturation_zero", src=0, sat=255)
        -> "test_saturation_zero__src0__sat255.jpg"

        snap_name("layer5::test_brightness_change", src=0, bri=220)
        -> "test_brightness_change__src0__bri220.jpg"
    """
    base = test_name.split("::")[-1]           # drop module path prefix if present
    base = re.sub(r'[^\w]', '_', base).strip('_')
    parts = [base] + [f"{k}{v}" for k, v in tags.items()]
    return "__".join(parts) + ".jpg"
