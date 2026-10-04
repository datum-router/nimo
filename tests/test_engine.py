"""Engine unit tests: oracle state, coverage maths, report schema, backends."""
from __future__ import annotations

import json

from nimo.engine import coverage, oracle
from nimo.llm import NullBackend, get_backend
from nimo.report import Action, ApkMeta, Bug, Report


# ── Oracle: state + progress (signal 2) ──

def _state(activity, sig):
    return oracle.State(activity=activity, ui_signature=sig, elements=[])


def test_progress_detected_on_activity_change():
    assert oracle.Oracle.progressed(_state("p/.A", "x"), _state("p/.B", "x"))


def test_progress_detected_on_ui_change_within_one_activity():
    assert oracle.Oracle.progressed(_state("p/.A", "x"), _state("p/.A", "y"))


def test_no_progress_when_nothing_changed():
    assert not oracle.Oracle.progressed(_state("p/.A", "x"), _state("p/.A", "x"))


def test_observe_builds_a_stable_signature_from_the_hierarchy(monkeypatch):
    elems = [{"label": "Save", "class": "Button"},
             {"label": "Name", "class": "EditText"}]
    monkeypatch.setattr(oracle.device, "dump_ui", lambda: elems)
    monkeypatch.setattr(oracle.device, "current_activity", lambda: "com.x/.Main")
    a = oracle.Oracle("com.x").observe()
    # Order must not change the signature — the element SET is what matters.
    monkeypatch.setattr(oracle.device, "dump_ui", lambda: list(reversed(elems)))
    b = oracle.Oracle("com.x").observe()
    assert a.ui_signature == b.ui_signature
    assert a.activity == "com.x/.Main"


def test_observe_survives_a_failed_ui_dump(monkeypatch):
    def boom():
        raise oracle.device.DeviceError("uiautomator died")
    monkeypatch.setattr(oracle.device, "dump_ui", boom)
    monkeypatch.setattr(oracle.device, "current_activity", lambda: None)
    st = oracle.Oracle("com.x").observe()
    assert st.elements == []


# ── Coverage: the two-tier story ──

def test_activity_coverage_normalises_relative_and_absolute_names():
    r = coverage.activity_coverage(
        declared_activities=[".Main", "com.x.Detail", ".Settings"],
        reached_components={"com.x/.Main", "com.x/com.x.Detail"},
        package="com.x")
    assert r["covered"] == 2
    assert r["total"] == 3
    assert r["percent"] == 66.7
    assert r["missed"] == ["com.x.Settings"]


def test_activity_coverage_is_zero_with_no_declared_activities():
    r = coverage.activity_coverage([], set(), "com.x")
    assert r["total"] == 0 and r["percent"] == 0.0


def test_activity_coverage_ignores_unreached_foreign_activities():
    r = coverage.activity_coverage(
        declared_activities=[".Main"],
        reached_components={"com.other/.Thing", "com.x/.Main"},
        package="com.x")
    assert r["covered"] == 1 and r["total"] == 1


def test_coverage_kind_is_labelled_so_claims_stay_honest():
    r = coverage.activity_coverage([".A"], {"com.x/.A"}, "com.x")
    assert r["kind"] == "activity", (
        "coverage must say which KIND it is — activity reach is not line coverage")


# ── Report schema ──

def test_report_round_trips_to_json():
    rep = Report(apk=ApkMeta(package="com.x"), mode="repro",
                 verdict="not_reproduced")
    data = json.loads(rep.to_json())
    assert data["verdict"] == "not_reproduced"
    assert data["apk"]["package"] == "com.x"
    assert data["coverage"]["kind"] == "none"


def test_report_html_shows_undelivered_gestures():
    rep = Report(apk=ApkMeta(package="com.x"), mode="discover",
                 gesture_trace=[Action(step=1, kind="pinch", target="c",
                                       delivered=False)])
    assert "UNSUPPORTED" in rep.to_html()


def test_report_html_escapes_untrusted_app_text():
    rep = Report(apk=ApkMeta(package="com.x"), mode="discover",
                 gesture_trace=[Action(step=1, kind="tap",
                                       target="<script>alert(1)</script>")])
    html = rep.to_html()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_bug_carries_its_audit_summary():
    b = Bug(fingerprint="a" * 12, kind="NPE", stack="x", legitimacy="PASS (6/6)")
    rep = Report(apk=ApkMeta(package="com.x"), mode="discover", discovered=[b])
    assert "PASS (6/6)" in rep.to_html()


# ── LLM backends ──

def test_null_backend_needs_no_key_and_is_deterministic():
    b = get_backend("null")
    assert isinstance(b, NullBackend)
    first = b.chat([{"role": "user", "content": "hi"}])
    second = b.chat([{"role": "user", "content": "different"}])
    assert first == second, "the CI backend must be deterministic"
    json.loads(first)  # must be a valid action document


def test_default_backend_is_pollinations_and_needs_no_key():
    b = get_backend()
    assert "pollinations" in b.base_url, (
        "the free default must stay Pollinations — no signup, no key")
    assert b.api_key == "not-needed"


def test_backend_honours_explicit_overrides():
    b = get_backend("openai", base_url="https://example.invalid/v1",
                    model="gpt-4o-mini", api_key="k")
    assert b.base_url == "https://example.invalid/v1"
    assert b.model == "gpt-4o-mini"
