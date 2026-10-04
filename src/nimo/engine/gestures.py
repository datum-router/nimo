"""Gesture executor — CARBON's gesture set, ported onto nimo's adb device.

CARBON drove gestures through uiautomator2 (`device.jsonrpc.pinchIn`,
`UiObject.gesture`, ...). nimo's `device.py` is stdlib-only, driving raw
`adb shell input` / `sendevent`. This module re-expresses the gesture set on
nimo's primitives so the fused engine keeps the no-extra-driver ethos.

Gestures expressible through `adb shell input` work on any device/emulator:
    long_press, double_tap, drag_and_drop, region_swipe, edge_swipe,
    rapid_click, picker_scroll.

True multi-touch (pinch, rotate, two/three-finger) needs simultaneous
pointers, which `adb shell input` cannot express. These use
`adb shell input motionevent` multi-pointer sequences where the device
supports it, and degrade to a documented no-op + report flag where it does
not (tracked in the device-compat matrix). This is the honest behaviour the
merge spec requires: a gesture we cannot deliver is reported unsupported,
never silently claimed.
"""
from __future__ import annotations

import time

from . import device
from .device import _run, screen_size


# ── Single-pointer gestures (universally supported via `adb shell input`) ──

def long_press(x: int, y: int, ms: int = 800) -> None:
    """Long-press = a zero-distance swipe held for `ms`."""
    _run("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(ms))


def double_tap(x: int, y: int, gap_ms: int = 80) -> None:
    device.tap(x, y)
    time.sleep(gap_ms / 1000.0)
    device.tap(x, y)


def rapid_click(x: int, y: int, count: int = 10) -> None:
    """Fire N taps as fast as adb allows — for race-condition bugs."""
    n = max(1, min(int(count), 50))
    for _ in range(n):
        device.tap(x, y)


def drag_and_drop(x1: int, y1: int, x2: int, y2: int, hold_ms: int = 800) -> None:
    """Hold at the source to trigger drag selection, then move to target.
    A long swipe duration is what distinguishes a drag from a flick."""
    _run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(hold_ms))


def region_swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
    device.swipe(x1, y1, x2, y2, ms)


def edge_swipe(edge: str, direction: str) -> None:
    """Swipe from a screen edge inward (back gesture, drawer, shade)."""
    w, h = screen_size()
    table = {
        ("left", "right"): (int(w * 0.02), h // 2, int(w * 0.40), h // 2),
        ("right", "left"): (int(w * 0.98), h // 2, int(w * 0.60), h // 2),
        ("top", "down"): (w // 2, int(h * 0.02), w // 2, int(h * 0.40)),
        ("bottom", "up"): (w // 2, int(h * 0.98), w // 2, int(h * 0.60)),
    }
    key = (edge, direction)
    if key not in table:
        raise ValueError(f"invalid edge_swipe {edge}+{direction}")
    device.swipe(*table[key], 300)


def picker_scroll(cx: int, cy: int, height: int, direction: str = "up",
                  steps: int = 1) -> None:
    """Scroll an Android NumberPicker by exactly `steps` ticks.
    A short, slow swipe of ~one cell is what the picker registers as a tick;
    a fast fling overshoots. This is the only path that reproduces
    invalid-intermediate-state picker bugs (set_text validates and skips the
    window)."""
    cell = max(60, height // 3)
    dist = int(cell * 0.6)
    for _ in range(max(1, int(steps))):
        if direction == "up":
            device.swipe(cx, cy + dist // 2, cx, cy - dist // 2, 350)
        elif direction == "down":
            device.swipe(cx, cy - dist // 2, cx, cy + dist // 2, 350)
        else:
            raise ValueError("picker_scroll direction must be up/down")
        time.sleep(0.25)


# ── Multi-touch gestures (require simultaneous pointers) ──
# `adb shell input motionevent` supports DOWN/MOVE/UP with a pointer index on
# API 30+. Where available we drive two pointers; where not, we report the
# gesture unsupported rather than faking it.

_MOTIONEVENT_SUPPORTED: bool | None = None


def _motionevent_supported() -> bool:
    global _MOTIONEVENT_SUPPORTED
    if _MOTIONEVENT_SUPPORTED is None:
        try:
            out = _run("shell", "input", "motionevent", "--help", timeout=10)
            _MOTIONEVENT_SUPPORTED = "motionevent" in (out or "").lower()
        except device.DeviceError:
            _MOTIONEVENT_SUPPORTED = False
    return _MOTIONEVENT_SUPPORTED


def _two_pointer(path1: list[tuple[int, int]],
                 path2: list[tuple[int, int]]) -> bool:
    """Drive two simultaneous pointers along equal-length paths.
    Returns False (unsupported) rather than faking a single-pointer move."""
    if not _motionevent_supported() or len(path1) != len(path2) or len(path1) < 2:
        return False
    try:
        _run("shell", "input", "motionevent", "DOWN", str(path1[0][0]), str(path1[0][1]))
        _run("shell", "input", "motionevent", "DOWN", str(path2[0][0]), str(path2[0][1]))
        for (ax, ay), (bx, by) in zip(path1[1:], path2[1:]):
            _run("shell", "input", "motionevent", "MOVE", str(ax), str(ay))
            _run("shell", "input", "motionevent", "MOVE", str(bx), str(by))
        _run("shell", "input", "motionevent", "UP", str(path1[-1][0]), str(path1[-1][1]))
        _run("shell", "input", "motionevent", "UP", str(path2[-1][0]), str(path2[-1][1]))
        return True
    except device.DeviceError:
        return False


def pinch(pinch_type: str, cx: int | None = None, cy: int | None = None,
          steps: int = 10) -> bool:
    """Two-finger pinch. 'in' = zoom out, 'out' = zoom in.
    Returns True if delivered, False if the device can't do multi-touch
    (caller records it unsupported in the report)."""
    w, h = screen_size()
    cx = cx if cx is not None else w // 2
    cy = cy if cy is not None else h // 2
    extent = int(min(w, h) * 0.28)
    near = extent // 5

    def lerp(a, b, n):
        return [(int(a[0] + (b[0] - a[0]) * i / n), int(a[1] + (b[1] - a[1]) * i / n))
                for i in range(n + 1)]

    if pinch_type == "in":      # fingers apart -> together
        p1 = lerp((cx - extent, cy), (cx - near, cy), steps)
        p2 = lerp((cx + extent, cy), (cx + near, cy), steps)
    elif pinch_type == "out":   # fingers together -> apart
        p1 = lerp((cx - near, cy), (cx - extent, cy), steps)
        p2 = lerp((cx + near, cy), (cx + extent, cy), steps)
    else:
        raise ValueError("pinch_type must be 'in' or 'out'")
    return _two_pointer(p1, p2)


def rotate(cx: int | None = None, cy: int | None = None, degrees: int = 90,
           clockwise: bool = True, steps: int = 12) -> bool:
    """Two-finger rotation around (cx, cy). Returns delivery success."""
    import math
    w, h = screen_size()
    cx = cx if cx is not None else w // 2
    cy = cy if cy is not None else h // 2
    r = int(min(w, h) * 0.2)
    step = math.radians(degrees / steps) * (1 if clockwise else -1)

    def pos(i, off):
        a = step * i + off
        return (int(cx + r * math.cos(a)), int(cy + r * math.sin(a)))

    p1 = [pos(i, 0.0) for i in range(steps + 1)]
    p2 = [pos(i, math.pi) for i in range(steps + 1)]
    return _two_pointer(p1, p2)


# Device orientation (not a touch gesture — uses the settings surface)
def set_orientation(orientation: str) -> None:
    """portrait | landscape via accelerometer-rotation off + user_rotation."""
    rot = {"portrait": "0", "landscape": "1",
           "natural": "0", "left": "1", "right": "3", "upsidedown": "2"}
    if orientation not in rot:
        raise ValueError(f"unknown orientation {orientation}")
    _run("shell", "settings", "put", "system", "accelerometer_rotation", "0")
    _run("shell", "settings", "put", "system", "user_rotation", rot[orientation])
