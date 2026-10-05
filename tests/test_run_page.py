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


def test_zero_coverage_is_distinguished_from_unmeasured_coverage():
    """`repro` mode runs no crawl, so total is 0 and there is nothing to report.

    Rendering that as a 0% ring read as a catastrophic result rather than
    "not measured in this mode" -- and since repro is now the DEFAULT mode,
    every first-time user would have seen it. A real 0-of-7 must still show
    0%, so the distinction is on `total`, not on `coverage_pct`.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "const measured = (s.total || 0) > 0" in html, (
        "the page must distinguish unmeasured coverage from zero coverage"
    )
    assert "not measured" in html or "not\n" in html
    assert "repro mode runs no crawl" in html, (
        "the user must be told WHY there is no number"
    )


def test_footer_is_a_full_width_band_outside_the_content_wrapper():
    """The footer sat INSIDE div.wrap, so its navy band was clipped to the
    content column and rendered as a stray bar mid-page."""
    html = RUN_PAGE.read_text(encoding="utf-8")
    body = html.split("<body>", 1)[1]
    footer_at = body.index("<footer>")
    # Everything before the footer must have closed the content wrapper.
    before = body[:footer_at]
    assert before.count("<div") <= before.count("</div"), (
        "the footer is still nested inside an unclosed container"
    )
    assert "footer .wrap{" in html, "the footer needs its own inner wrap"


def test_progress_uses_step_count_when_there_are_no_screens():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "byScreens" in html, (
        "repro mode reports steps, not screens; a screens-only bar sat at "
        "zero for the default mode"
    )
    assert "steps driven on the device" in html


def test_device_panel_says_what_is_actually_on_screen():
    """Before launch the frame is Android's launcher, not the app.

    Labelling it "live screen" made a booting device indistinguishable from
    a stuck one -- the operator reported it as "shows nothing".
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "this is the launcher, not your app yet" in html


# ---------------------------------------------------------------------------
# Landing page call-to-action targets
# ---------------------------------------------------------------------------

LANDING = REPO_ROOT / "docs" / "index.html"


def test_primary_calls_to_action_open_the_product_not_the_repo():
    """"Get started" and "Try the demo" both pointed at github.com.

    The hosted run page IS the demo; sending a prospective user to a source
    tree instead is the single worst link on the page.
    """
    html = LANDING.read_text(encoding="utf-8")
    hero = html.split('class="cta"', 1)[1][:600]
    assert 'href="run.html"' in hero, (
        "the hero's primary action must open the run page"
    )
    nav = re.search(r"<nav.*?</nav>", html, re.S).group(0)
    cta = re.search(r'<a class="btn[^"]*navcta"[^>]*href="([^"]+)"', nav)
    assert cta and cta.group(1) == "run.html", (
        f"the nav CTA points at {cta.group(1) if cta else 'nothing'}"
    )
    # GitHub is still linked -- as an ordinary link, not as the main action.
    assert "github.com/datum-router/nimo" in html


def test_the_nav_cta_is_actually_visible():
    """It was #0F1111 on the #232F3E nav: 1.27:1."""
    html = LANDING.read_text(encoding="utf-8")
    nav = re.search(r"<nav.*?</nav>", html, re.S).group(0)
    assert "style=" not in re.search(
        r'<a class="btn[^"]*navcta"[^>]*>', nav).group(0), (
        "the nav CTA must be styled by a rule the contrast audit can see, "
        "not by an inline style it cannot"
    )
    assert ".navcta{" in html, "the nav CTA needs its own visible treatment"


# ---------------------------------------------------------------------------
# Verdict status
# ---------------------------------------------------------------------------

def test_every_verdict_maps_to_a_finished_pill_style():
    """The pill showed the orange RUNNING style for most finished runs.

    It was `clean ? done : failed ? fail : "run"`, so `reproduced` and
    `not_reproduced` -- the two most common outcomes -- rendered a finished
    run as though it were still going. That is what "verdict status is
    broken" looked like.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    block = re.search(r"const titles = \{(.*?)\};", html, re.S)
    assert block, "verdict table not found"
    table = block.group(1)
    for verdict in ("bugs_found", "reproduced", "clean", "not_reproduced",
                    "inconclusive", "failed", "backend_unavailable"):
        assert verdict in table, f"{verdict} has no verdict mapping"
    # Each entry must carry a pill class, and never the running one.
    entries = re.findall(r'\[\s*"[^"]*",\s*"(v-[a-z]+)",\s*"([a-z]+)"\s*\]', table)
    assert len(entries) >= 7, f"expected a pill class per verdict, got {entries}"
    for _, pill in entries:
        assert pill in {"done", "fail", "warn"}, (
            f"pill class {pill!r} is not a finished-run style"
        )
    assert ".pill.warn{" in html, "the amber finished style must exist"


def test_verdict_rendering_survives_a_missing_verdict():
    """summary.json can land without a verdict; the card must not throw."""
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert 'const verdict = s.verdict || "failed"' in html, (
        "s.verdict.replace() on undefined would throw and render nothing"
    )


# ---------------------------------------------------------------------------
# Detailed report
# ---------------------------------------------------------------------------

def test_detailed_report_covers_the_requested_metrics():
    html = RUN_PAGE.read_text(encoding="utf-8")
    for label in ("Activity coverage", "Code coverage", "Screens reached",
                  "Steps driven", "Deep links / intents", "AI guidance"):
        assert label in html, f"detailed report is missing {label!r}"
    assert "renderMetrics" in html and 'id="r-metrics"' in html


def test_unmeasured_metrics_say_so_rather_than_showing_zero():
    """A run with no instrumented build has NO line coverage.

    Printing 0% for it is a false statement about the app, not a missing
    number -- the same mistake the coverage donut made.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert 'measured ? big : "not measured"' in html, (
        "each tile must render not-measured from its own flag"
    )
    assert ".metric.unmeasured{" in html


# ---------------------------------------------------------------------------
# Session + per-user history
# ---------------------------------------------------------------------------

def test_session_persists_and_can_be_ended():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "SESSION_KEY" in html and "loadSession()" in html, (
        "the signed-in identity must survive a reload"
    )
    assert "function signOut()" in html, "a session must be endable"
    assert 'localStorage.removeItem("nimo_gh_token")' in html, (
        "signing out must drop the token too, not just the display name"
    )


def test_history_is_filtered_to_the_signed_in_account():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "triggering_actor" in html, (
        "runs must be attributed to the account that dispatched them, not "
        "to the repository owner"
    )
    assert "loadHistory" in html and 'id="h-table"' in html


def test_history_does_not_claim_privacy_it_cannot_provide():
    """The live branch is public; this is attribution, not isolation.

    Implying otherwise would be the most damaging kind of UI copy in a
    product whose whole pitch is that it does not overstate what it knows.
    """
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "shareable, not private" in html, (
        "the history panel must state that results are published publicly"
    )


def test_a_past_run_can_be_reopened_by_url():
    html = RUN_PAGE.read_text(encoding="utf-8")
    assert "openRunFromQuery" in html and 'URLSearchParams' in html, (
        "history rows link to ?run=<id>; that must actually open the result"
    )


def test_runs_are_attributed_in_the_published_summary():
    sh = (REPO_ROOT / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "GITHUB_ACTOR" in sh, (
        "the published summary must record who dispatched the run"
    )
