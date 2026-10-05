"""Guards on the detailed report block in summary.json.

The rule these all serve: a figure that was not measured must say so, never
render as zero. "0% code coverage" on a run that never instrumented the APK
is a false statement about the app, where "not measured" is simply the
truth -- and this product's entire claim is that it does not overstate what
it knows.
"""

from __future__ import annotations

import json
from pathlib import Path

from nimo.engine import coverage, summarize

REPO = Path(__file__).resolve().parents[1]

DEVICE = {"android_version": "13.0.0", "arch": "amd64", "runtime": "emulator"}


def _summary(tmp_path, report: dict) -> dict:
    rp = tmp_path / "pipeline_report.json"
    rp.write_text(json.dumps(report))
    return summarize.build(str(rp), DEVICE, str(tmp_path / "summary.json"))


def test_repro_only_run_reports_unmeasured_coverage(tmp_path):
    s = _summary(tmp_path, {
        "package": "bander.notepad", "app": "Notepad",
        "map": {"activities": ["a"] * 7},
        "repro": {"verdict": "not_reproduced", "steps_taken": 25,
                  "steps_in_app": 25, "off_app_steps": []},
        "wall_seconds": 97.5,
    })
    m = s["metrics"]
    assert m["activity_coverage"]["measured"] is False, (
        "repro mode runs no crawl; there is no activity coverage to report"
    )
    assert m["activity_coverage"]["pct"] is None, "must be absent, not 0"
    assert m["line_coverage"]["measured"] is False
    assert m["line_coverage"]["pct"] is None
    assert m["steps"]["measured"] is True
    assert m["steps"]["taken"] == 25
    assert m["steps"]["off_app"] == 0


def test_off_app_excursions_are_surfaced(tmp_path):
    """A verdict reached after escaping the app is weak evidence."""
    s = _summary(tmp_path, {
        "package": "p", "map": {"activities": []},
        "repro": {"verdict": "not_reproduced", "steps_taken": 25,
                  "steps_in_app": 7,
                  "off_app_steps": [{"step": i} for i in range(18)]},
    })
    assert s["metrics"]["steps"]["off_app"] == 18
    assert s["metrics"]["steps"]["in_app"] == 7


def test_discovery_run_reports_measured_activity_coverage(tmp_path):
    s = _summary(tmp_path, {
        "package": "p", "app": "App",
        "map": {"activities": ["a", "b", "c", "d"]},
        "discovery": {
            "visited_activities": ["p/.A", "p/.B"],
            "coverage_pct": 50.0, "bugs": [], "unreachable": [{"activity": "p/.C"}],
            "intent_sweep": {"targets": 3, "reached": ["x"]},
        },
    })
    m = s["metrics"]
    assert m["activity_coverage"]["measured"] is True
    assert m["activity_coverage"]["pct"] == 50.0
    assert m["activity_coverage"]["visited"] == 2
    assert m["activity_coverage"]["total"] == 4
    assert m["activity_coverage"]["unreachable"] == 1
    assert m["screens"]["measured"] is True
    assert m["screens"]["reached"] == 2
    assert m["screens"]["names"] == ["p/.A", "p/.B"]
    assert m["intent_sweep"] == {"measured": True, "targets": 3, "reached": 1}


def test_degradation_is_carried_into_the_metrics(tmp_path):
    s = _summary(tmp_path, {
        "package": "p", "map": {"activities": []},
        "repro": {"verdict": "not_reproduced"},
        "ai_guidance": False, "degraded": True,
        "degraded_reason": "HTTP 402 Payment Required",
    })
    assert s["metrics"]["ai_guidance"] is False
    assert s["metrics"]["degraded"] is True
    assert "402" in s["metrics"]["degraded_reason"]


# ---- JaCoCo parsing -------------------------------------------------------

JACOCO_XML = """<?xml version="1.0"?>
<report name="app">
  <package name="com/x">
    <class name="com/x/A">
      <counter type="LINE" missed="5" covered="5"/>
    </class>
  </package>
  <counter type="INSTRUCTION" missed="300" covered="700"/>
  <counter type="BRANCH" missed="40" covered="60"/>
  <counter type="LINE" missed="25" covered="75"/>
</report>
"""


def test_jacoco_totals_are_read_from_the_report_level(tmp_path):
    """"converted" used to be reported with no NUMBER attached.

    The frontend then had a measured flag and nothing to display, so a run
    that genuinely measured coverage showed an em dash.
    """
    xml = tmp_path / "coverage.xml"
    xml.write_text(JACOCO_XML)
    totals = coverage._jacoco_totals(str(xml))
    assert totals["line"] == {"covered": 75, "total": 100, "pct": 75.0}
    assert totals["branch"]["pct"] == 60.0
    assert totals["instruction"]["pct"] == 70.0


def test_per_class_counters_are_not_double_counted(tmp_path):
    """Only report-level counters count; the nested ones would inflate."""
    xml = tmp_path / "coverage.xml"
    xml.write_text(JACOCO_XML)
    totals = coverage._jacoco_totals(str(xml))
    # The nested class counter says 5/10; the report-level one says 75/100.
    assert totals["line"]["total"] == 100


def test_unreadable_jacoco_xml_is_not_reported_as_zero(tmp_path):
    xml = tmp_path / "broken.xml"
    xml.write_text("<report><not-closed>")
    assert coverage._jacoco_totals(str(xml)) == {}
    assert coverage._jacoco_totals(str(tmp_path / "missing.xml")) == {}


def test_line_coverage_is_unmeasured_without_a_percentage(tmp_path):
    """A conversion can succeed and still produce no readable counters."""
    s = _summary(tmp_path, {
        "package": "p", "map": {"activities": ["a"]},
        "discovery": {"visited_activities": ["p/.A"], "coverage_pct": 100.0,
                      "bugs": [], "unreachable": [],
                      "coverage": {"converted": True, "xml": "coverage.xml"}},
    })
    assert s["metrics"]["line_coverage"]["measured"] is False, (
        "converted-but-numberless must not read as measured"
    )
    assert s["metrics"]["line_coverage"]["ec_collected"] is True


def test_line_coverage_is_measured_when_numbers_exist(tmp_path):
    s = _summary(tmp_path, {
        "package": "p", "map": {"activities": ["a"]},
        "discovery": {"visited_activities": ["p/.A"], "coverage_pct": 100.0,
                      "bugs": [], "unreachable": [],
                      "coverage": {"converted": True, "pct": 75.0,
                                   "covered": 75, "total": 100,
                                   "basis": "line"}},
    })
    line = s["metrics"]["line_coverage"]
    assert line["measured"] is True
    assert (line["pct"], line["covered"], line["total"]) == (75.0, 75, 100)
    assert line["basis"] == "line"


def test_discover_mode_reports_crawl_actions_as_steps(tmp_path):
    """Live run #35 reported 0 steps after a real 358-second crawl.

    The two halves of the engine count different things -- the repro loop
    walks a fixed step budget, the crawler takes as many actions as its
    budget allows -- and reading only repro steps left this unmeasured for
    every discover run. `basis` says which is being reported, so a bare
    number is never ambiguous.
    """
    s = _summary(tmp_path, {
        "package": "p", "map": {"activities": ["a", "b"]},
        "discovery": {"visited_activities": ["p/.A"], "coverage_pct": 50.0,
                      "bugs": [], "unreachable": [],
                      "actions_taken": 37, "deep_links_fired": 4},
    })
    st = s["metrics"]["steps"]
    assert st["measured"] is True
    assert st["taken"] == 37
    assert st["basis"] == "crawl actions"
    assert st["deep_links_fired"] == 4


def test_repro_steps_keep_their_own_basis(tmp_path):
    s = _summary(tmp_path, {
        "package": "p", "map": {"activities": []},
        "repro": {"verdict": "not_reproduced", "steps_taken": 25,
                  "steps_in_app": 20, "off_app_steps": [{"step": 1}] * 5},
    })
    st = s["metrics"]["steps"]
    assert st["basis"] == "repro steps"
    assert (st["taken"], st["in_app"], st["off_app"]) == (25, 20, 5)


def test_a_run_with_neither_half_is_unmeasured(tmp_path):
    s = _summary(tmp_path, {"package": "p", "map": {"activities": []}})
    assert s["metrics"]["steps"]["measured"] is False
