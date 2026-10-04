"""Gesture executor tests — no device required.

Every gesture is asserted by the adb command sequence it produces. This is how
we keep CARBON's gesture semantics after porting them off uiautomator2 onto
raw `adb shell input`.
"""
from __future__ import annotations

import pytest

from nimo.engine import device, gestures


@pytest.fixture()
def adb(monkeypatch):
    """Capture every adb invocation instead of running it."""
    calls: list[tuple[str, ...]] = []

    def fake_run(*args, timeout: int = 60):
        calls.append(args)
        return ""

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr(gestures, "_run", fake_run)
    monkeypatch.setattr(gestures, "screen_size", lambda: (1080, 2400))
    monkeypatch.setattr(device, "tap",
                        lambda x, y: calls.append(("shell", "input", "tap", str(x), str(y))))
    monkeypatch.setattr(device, "swipe",
                        lambda x1, y1, x2, y2, ms=300: calls.append(
                            ("shell", "input", "swipe", str(x1), str(y1),
                             str(x2), str(y2), str(ms))))
    monkeypatch.setattr(gestures, "device", device)
    return calls


def test_long_press_is_a_held_zero_distance_swipe(adb):
    gestures.long_press(100, 200, ms=900)
    assert adb == [("shell", "input", "swipe", "100", "200", "100", "200", "900")]


def test_double_tap_sends_two_taps(adb):
    gestures.double_tap(50, 60, gap_ms=1)
    taps = [c for c in adb if c[2] == "tap"]
    assert len(taps) == 2
    assert all(c[3:] == ("50", "60") for c in taps)


def test_rapid_click_is_capped_at_fifty(adb):
    gestures.rapid_click(10, 10, count=500)
    assert len([c for c in adb if c[2] == "tap"]) == 50


def test_rapid_click_sends_at_least_one(adb):
    gestures.rapid_click(10, 10, count=0)
    assert len([c for c in adb if c[2] == "tap"]) == 1


def test_drag_and_drop_uses_a_long_swipe_duration(adb):
    gestures.drag_and_drop(10, 20, 30, 40, hold_ms=800)
    assert adb == [("shell", "input", "swipe", "10", "20", "30", "40", "800")]


def test_edge_swipe_starts_at_the_edge(adb):
    gestures.edge_swipe("left", "right")
    (_, _, _, x1, y1, x2, y2, _dur) = adb[0]
    assert int(x1) < int(x2), "a left-edge swipe must travel inward"
    assert int(x1) <= int(1080 * 0.05), "must start at the very edge"


def test_edge_swipe_rejects_an_invalid_combination(adb):
    with pytest.raises(ValueError):
        gestures.edge_swipe("left", "up")


def test_picker_scroll_emits_one_slow_swipe_per_tick(adb):
    gestures.picker_scroll(cx=400, cy=900, height=495, direction="up", steps=3)
    swipes = [c for c in adb if c[2] == "swipe"]
    assert len(swipes) == 3, "one swipe per tick — a fling would overshoot"
    assert swipes[0][-1] == "350", "must be slow enough to register as a drag"


def test_picker_scroll_rejects_a_bad_direction(adb):
    with pytest.raises(ValueError):
        gestures.picker_scroll(cx=1, cy=1, height=300, direction="sideways")


def test_set_orientation_disables_auto_rotate_first(adb):
    gestures.set_orientation("landscape")
    assert adb[0][:5] == ("shell", "settings", "put", "system",
                          "accelerometer_rotation")
    assert adb[1][-1] == "1"


def test_set_orientation_rejects_an_unknown_value(adb):
    with pytest.raises(ValueError):
        gestures.set_orientation("sideways")


# ── Multi-touch honesty: an undeliverable gesture reports False, never fakes ──

def test_pinch_reports_false_when_the_device_lacks_motionevent(monkeypatch):
    monkeypatch.setattr(gestures, "screen_size", lambda: (1080, 2400))
    monkeypatch.setattr(gestures, "_motionevent_supported", lambda: False)
    assert gestures.pinch("out") is False, (
        "an undeliverable multi-touch gesture must report failure so the "
        "report can mark it unsupported — never silently claim success")


def test_pinch_drives_two_pointers_when_supported(monkeypatch):
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(gestures, "screen_size", lambda: (1080, 2400))
    monkeypatch.setattr(gestures, "_motionevent_supported", lambda: True)
    monkeypatch.setattr(gestures, "_run",
                        lambda *a, timeout=60: calls.append(a) or "")
    assert gestures.pinch("out", steps=3) is True
    kinds = [c[3] for c in calls if c[:3] == ("shell", "input", "motionevent")]
    assert kinds.count("DOWN") == 2, "two fingers must go down"
    assert kinds.count("UP") == 2, "two fingers must come up"
    assert kinds.count("MOVE") >= 2


def test_pinch_rejects_an_invalid_type(monkeypatch):
    monkeypatch.setattr(gestures, "screen_size", lambda: (1080, 2400))
    with pytest.raises(ValueError):
        gestures.pinch("sideways")


def test_rotate_reports_false_without_multitouch(monkeypatch):
    monkeypatch.setattr(gestures, "screen_size", lambda: (1080, 2400))
    monkeypatch.setattr(gestures, "_motionevent_supported", lambda: False)
    assert gestures.rotate(degrees=90) is False
