"""Guards on the UI dump (``device.dump_ui``).

The dump decides what the engine can SEE. A target missing from it is
invisible to the LLM and to the deterministic explorer alike, and the
failure is silent: the run keeps going and simply never touches that
control, so the only symptom is a weaker result.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from nimo.engine import device

REPO = Path(__file__).resolve().parents[1]

# An overflow menu exactly as Android emits it: clickable="true" on the
# item's container, the label on a non-clickable TextView child. This is the
# overwhelmingly common shape -- list rows, menu items and Buttons with
# separate text views all look like this.
MENU_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false">
    <node class="android.widget.ListView" resource-id="android:id/list"
          bounds="[600,200][1080,700]" clickable="false">
      <node class="android.widget.LinearLayout" bounds="[600,200][1080,340]" clickable="true">
        <node class="android.widget.TextView" text="Settings"
              resource-id="android:id/title" bounds="[620,240][900,300]" clickable="false"/>
      </node>
      <node class="android.widget.LinearLayout" bounds="[600,340][1080,480]" clickable="true">
        <node class="android.widget.TextView" text="Search"
              resource-id="android:id/title" bounds="[620,380][900,440]" clickable="false"/>
      </node>
    </node>
    <node class="android.widget.TextView" text="Not a control"
          bounds="[0,1800][500,1860]" clickable="false"/>
  </node>
</hierarchy>
"""


def _dump(monkeypatch, xml: str) -> list[dict]:
    """Run the real dump_ui against a canned hierarchy."""
    monkeypatch.setattr(device, "_run", lambda *a, **k: "")
    monkeypatch.setattr(ET, "parse", lambda _p: ET.ElementTree(ET.fromstring(xml)))
    monkeypatch.setattr(device.os, "unlink", lambda _p: None)
    return device.dump_ui()


def test_menu_items_are_tappable(monkeypatch):
    """CI run #33: every overflow-menu item came back clickable=false.

    The explorer therefore saw an empty screen, pressed Back, tapped "More
    options" again, and oscillated between those two actions for ten steps.
    The flag was being read off the labelled TextView, which Android marks
    non-clickable because the touch target is its parent.
    """
    elems = _dump(monkeypatch, MENU_XML)
    by_label = {e["label"]: e for e in elems}
    for label in ("Settings", "Search"):
        assert label in by_label, f"{label} missing from the dump entirely"
        assert by_label[label]["clickable"] is True, (
            f"{label} reported untappable; its clickable ancestor was ignored"
        )


def test_genuine_non_controls_stay_non_clickable(monkeypatch):
    """Inheritance must not mark the whole screen tappable.

    If everything were clickable the explorer would waste its budget on
    headings and labels, which is the opposite failure.
    """
    elems = _dump(monkeypatch, MENU_XML)
    by_label = {e["label"]: e for e in elems}
    assert by_label["Not a control"]["clickable"] is False


def test_the_distinction_is_preserved(monkeypatch):
    """A caller must still be able to tell inherited from genuine."""
    elems = _dump(monkeypatch, MENU_XML)
    by_label = {e["label"]: e for e in elems}
    assert by_label["Settings"]["self_clickable"] is False
    assert by_label["Settings"]["clickable"] is True


def test_tap_coordinates_are_unchanged_by_inheritance(monkeypatch):
    """Inheriting the flag must not move where a tap lands.

    The tap goes to the LABEL's centre, which lies inside the clickable
    ancestor's bounds -- so this changes what is considered, not where it
    is touched.
    """
    elems = _dump(monkeypatch, MENU_XML)
    s = next(e for e in elems if e["label"] == "Settings")
    assert (s["x"], s["y"]) == (760, 270), (
        f"tap point moved to {(s['x'], s['y'])}; expected the label's centre"
    )
    # ... and that point is inside the clickable parent [600,200][1080,340].
    assert 600 <= s["x"] <= 1080 and 200 <= s["y"] <= 340


def test_every_labelled_node_is_still_reported(monkeypatch):
    """The walk was rewritten from tree.iter(); it must not drop nodes."""
    elems = _dump(monkeypatch, MENU_XML)
    labels = {e["label"] for e in elems}
    assert {"Settings", "Search", "Not a control", "list"} <= labels, (
        f"nodes lost in the rewritten traversal: {labels}"
    )
