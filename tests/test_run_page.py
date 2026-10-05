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
