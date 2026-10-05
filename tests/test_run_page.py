"""Guards on the hosted run page (``docs/run.html``).

A device run finishes in roughly two to four minutes. The page is the only
thing the user watches while that happens, so its refresh cadence *is* the
product's perceived speed. An earlier version polled job status every 60s and
waited 15s between summary attempts, which left a finished 2.4-minute run
showing "wrapping up" for over two minutes -- the page was slower than the
pipeline it reported on.

These tests parse the real file rather than a copy, so they fail if the
cadence regresses. They are plain-text assertions on purpose: no JS runtime
and no new dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_PAGE = REPO_ROOT / "docs" / "run.html"

# Upper bounds in milliseconds. Each is well inside GitHub's authenticated
# rate limit (5000 req/hr): job polling at 10s is ~360 calls/hr.
MAX_INTERVALS_MS = {
    "findRun": 5_000,   # attach to the run promptly after dispatch
    "jobs": 15_000,     # step list must not lag a minute behind reality
    "prog": 10_000,     # runner republishes progress.json every 20s
}


@pytest.fixture(scope="module")
def page_source() -> str:
    assert RUN_PAGE.is_file(), f"missing run page: {RUN_PAGE}"
    return RUN_PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("fn,limit_ms", sorted(MAX_INTERVALS_MS.items()))
def test_poll_interval_is_responsive(page_source: str, fn: str, limit_ms: int) -> None:
    """Each poller must tick at least as often as its documented bound."""
    match = re.search(rf"setInterval\(\s*{fn}\s*,\s*(\d+)\s*\)", page_source)
    assert match, f"no setInterval({fn}, ...) found in {RUN_PAGE.name}"
    actual = int(match.group(1))
    assert actual <= limit_ms, (
        f"{fn} polls every {actual}ms; must be <= {limit_ms}ms or the tracker "
        f"appears frozen during a ~2-4 min run"
    )


def test_elapsed_clock_is_present(page_source: str) -> None:
    """A ticking elapsed time is what distinguishes 'working' from 'hung'."""
    assert re.search(r"setInterval\(\s*tick\s*,\s*1000\s*\)", page_source), (
        "no 1s elapsed-time ticker; without a moving number the tracker reads "
        "as hung between poll ticks"
    )
    assert "elapsed" in page_source, "elapsed label missing from the tracker"


def test_summary_wait_is_bounded_and_brief(page_source: str) -> None:
    """Total post-run summary wait must stay under ~60s.

    summary.json is pushed just before the job ends, so it is normally already
    on the branch when we first look; a long backoff only delays the verdict.
    """
    attempts = re.search(r"for\s*\(\s*let\s+i\s*=\s*0;\s*i\s*<\s*(\d+)\s*&&\s*!s;", page_source)
    delay = re.search(r"setTimeout\(\s*r\s*,\s*(\d+)\s*\)\s*\)\s*;?\s*\n\s*\}", page_source)
    assert attempts, "summary retry loop not found"
    assert delay, "summary retry delay not found"
    total_ms = int(attempts.group(1)) * int(delay.group(1))
    assert total_ms <= 60_000, (
        f"summary polling can block for {total_ms/1000:.0f}s after the run "
        f"already finished; keep it under 60s"
    )


def test_dispatch_errors_surface_github_reason(page_source: str) -> None:
    """A bare status code sent the user (and me) on a long hunt for a 422.

    GitHub puts the actual cause in the response body; discarding it is what
    turned "the workflow is disabled" into an unexplained error.
    """
    assert "GitHub API error" in page_source
    # The handler must read the body, not just the status.
    assert re.search(r"\.json\(\)|\.text\(\)", page_source), "error path reads no response body"
    assert "Enable workflow" in page_source or "disabled" in page_source, (
        "no hint pointing at a disabled workflow, the most common 422 cause"
    )


# ---------------------------------------------------------------------------
# Live log console
# ---------------------------------------------------------------------------
# Before this existed the page showed a screenshot and a screen count but no
# way to see WHAT nimo was doing, because pipeline.log was published only
# after the run finished. "Better visibility" is mostly this panel.

def test_page_streams_the_device_log_live():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "logTail" in html, "the page must stream the device log"
    assert "pipeline.log" in html and "logview" in html
    assert "logTail();" in html, (
        "logTail must be wired into the polling loop, not merely defined"
    )


def test_harness_publishes_the_log_during_the_run():
    """Publishing only at the end is what made the log useless while waiting."""
    sh = (REPO_ROOT / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    pusher = sh.split("PUSHER=$!")[0]
    assert "pipeline.log" in pusher, (
        "the background publisher must copy the log every cycle, not just "
        "once the run is over"
    )
    assert "tail -c" in pusher, (
        "the published log must be size-capped: a pathological run would "
        "otherwise force-push a huge file to the live branch every cycle"
    )


def test_log_noise_is_filtered_but_never_discarded():
    """Evidence must stay reachable.

    The emulator's swiftshader spam and Android's 20-line SecurityException
    stack traces bury the signal, so they are hidden BY DEFAULT -- but a
    product whose moat is honesty must not make evidence unreachable, so the
    raw view is one checkbox away and the count of hidden lines is shown.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "LOG_NOISE" in html and "ColorBuffer" in html
    assert 'id="log-raw"' in html, "a raw view must remain available"
    assert "noise lines hidden" in html, (
        "the page must disclose that it is hiding lines, not hide silently"
    )


def test_log_filters_ignore_the_timestamp_prefix():
    """Lines carry "[mm:ss] "; patterns must match the message, not the clock.

    Without stripping it, every `^`-anchored pattern silently stops matching
    and the console degrades to unclassified grey text with no filtering --
    a regression no other assertion here would catch.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "LOG_TS" in html, "the timestamp prefix must be stripped before matching"
    assert html.count("replace(LOG_TS") >= 2, (
        "both the classifier and the noise filter must strip the prefix"
    )


def test_log_view_does_not_yank_the_scroll_position():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "atBottom" in html, (
        "the console must only auto-follow when already at the tail, or it "
        "fights a user reading back through the log"
    )


# ---------------------------------------------------------------------------
# Mode cost disclosure
# ---------------------------------------------------------------------------

def test_modes_disclose_their_time_cost():
    """The page defaulted to the SLOWEST mode and showed no duration.

    `pipeline` runs Path A then Path B: measured at 7.6 min in CI run #28,
    against ~3 min for `repro` alone. A user who picked the default and then
    waited eight minutes had been given no way to know that was the choice
    they were making.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    modes = re.search(r'<div class="modes">(.*?)</div>', html, re.S)
    assert modes, "mode selector not found"
    block = modes.group(1)
    for mode in ("repro", "discover", "pipeline"):
        assert f'value="{mode}"' in block, f"{mode} mode missing"
    assert block.count("min ·") >= 3, (
        "every mode must state roughly how long it takes"
    )
    checked = re.search(r'value="(\w+)" checked', block)
    assert checked and checked.group(1) == "repro", (
        "the fastest mode must be the default; 'pipeline' made the first "
        "experience an eight-minute wait"
    )


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

def test_page_uses_the_light_amazon_palette():
    html = RUN_PAGE.read_text(encoding="utf-8")
    for colour in ("#232F3E", "#FF9900", "#0F1111", "#EAEDED", "#007185"):
        assert colour in html, f"Amazon palette colour {colour} missing"


def test_no_dark_surfaces_leak_back_in():
    """The previous theme's near-black backgrounds must stay gone.

    Checked as literals because a reintroduced dark card would still render
    'fine' in isolation -- only side by side with the light chrome does it
    look broken, which no automated check would otherwise notice.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    banned = ["#0b0e17", "#101425", "#05070d", "#1e2540", "#e8ecf8", "#7c6cf0"]
    found = [c for c in banned if c in html.lower()]
    assert not found, f"dark-theme colours still present: {found}"
    body = re.search(r"\nbody\{([^}]*)\}", html)
    assert body and "--page" in body.group(1), (
        "body must use the light page background variable"
    )


def test_animation_respects_reduced_motion():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in html, (
        "the live view animates; it must honour the OS setting"
    )
