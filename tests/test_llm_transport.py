"""LLM transport behaviour.

Every case here was observed for real against the live Pollinations endpoint
during an end-to-end CI run, where a single HTTP 500 destroyed a 20-minute
emulator run with a bare urllib traceback.
"""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from nimo.llm import get_backend
from nimo.llm.client import LLMClient, LLMUnavailable

REPO = Path(__file__).resolve().parents[1]


def _ok_response(text="{}"):
    body = json.dumps({"choices": [{"message": {"content": text}}]}).encode()

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _R(body)


def _http_error(code, reason="boom"):
    return urllib.error.HTTPError(
        "https://x.invalid/chat/completions", code, reason, {}, io.BytesIO(b"{}")
    )


def test_no_auth_header_when_no_key(monkeypatch):
    """An absent key must mean an absent header, not a placeholder one.

    `Authorization: Bearer not-needed` is what made Pollinations classify the
    request as authenticated and refuse it.
    """
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.headers)
        return _ok_response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    LLMClient(base_url="https://x.invalid", api_key="").chat([{"role": "user", "content": "hi"}])
    keys = {k.lower() for k in seen["headers"]}
    assert "authorization" not in keys, seen["headers"]


def test_auth_header_present_when_key_configured(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _ok_response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    LLMClient(base_url="https://x.invalid", api_key="secret").chat(
        [{"role": "user", "content": "hi"}]
    )
    assert seen["headers"].get("authorization") == "Bearer secret"


def test_transient_500_is_retried_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(500, "Internal Server Error")
        return _ok_response('{"action": "back"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = LLMClient(base_url="https://x.invalid", retries=3, backoff=0).chat(
        [{"role": "user", "content": "hi"}]
    )
    assert out == '{"action": "back"}'
    assert calls["n"] == 3, "a transient 500 must not kill the run"


def test_anonymous_402_is_retried_then_succeeds(monkeypatch):
    """On a free tier, 402 means "not right now" -- not "you must pay".

    Measured against Pollinations on 2026-10-05: 402, then 500 ``ENOSPC``,
    then HTTP 200, inside one minute; minutes later 12 consecutive calls
    succeeded. The old build treated that blip as terminal, which threw away
    a booted emulator and a 25-step run, and told the operator they had hit a
    hard paywall that did not exist.
    """
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(402, "Payment Required")
        return _ok_response('{"action":"back"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = LLMClient(base_url="https://x.invalid", api_key="", retries=5, backoff=0).chat(
        [{"role": "user", "content": "hi"}]
    )
    assert json.loads(out)["action"] == "back"
    assert calls["n"] == 3, "an anonymous 402 must be retried, not fatal"


def test_keyed_402_is_terminal_and_explains_itself(monkeypatch):
    """With a key configured, 402 is a real billing state.

    The operator has an account, so retrying cannot fix it and would only
    waste device time. Fail fast and name the way out.
    """
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(402, "Payment Required")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(LLMUnavailable) as e:
        LLMClient(base_url="https://x.invalid", api_key="secret", retries=3, backoff=0).chat(
            [{"role": "user", "content": "hi"}]
        )
    assert calls["n"] == 1, "a keyed 402 is a billing state; retrying wastes device time"
    msg = str(e.value)
    assert "402" in msg
    assert "NIMO_BASE_URL" in msg, "the error must name the way out"


@pytest.mark.parametrize(
    "code,keyed,expected",
    [
        (402, False, True),   # free tier throttle
        (402, True, False),   # real billing state
        (408, False, True),   # server-side timeout
        (429, True, True),    # rate limit, regardless of key
        (500, True, True),
        (503, False, True),
        (401, False, False),  # genuinely misconfigured
        (404, False, False),  # wrong base_url
        (400, False, False),  # malformed request
    ],
)
def test_retry_classification(code, keyed, expected):
    c = LLMClient(base_url="https://x.invalid", api_key="k" if keyed else "")
    assert c._retryable(code) is expected, f"HTTP {code} keyed={keyed}"


def test_backoff_is_capped(monkeypatch):
    """Uncapped doubling would sleep for minutes on the last attempt.

    The emulator sits idle during that sleep, burning the job's budget.
    """
    slept: list[float] = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 6:
            raise _http_error(503, "Service Unavailable")
        return _ok_response('{"action":"back"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    LLMClient(base_url="https://x.invalid", retries=6, backoff=1.5,
              max_backoff=20.0).chat([{"role": "user", "content": "hi"}])
    assert slept, "expected backoff sleeps"
    # 20s cap plus the +-20% jitter band.
    assert max(slept) <= 20.0 * 1.2 + 0.01, f"backoff exceeded its cap: {slept}"


def test_cli_reports_backend_outage_without_a_traceback(monkeypatch, capsys):
    """A 40-line urllib traceback is not a product surface.

    Exit 3 is deliberately distinct from 2, which `nimo repro` already uses
    for both a `not_reproduced` verdict and an argument error -- CI must be
    able to tell a provider outage from a genuine finding.
    """
    from nimo import cli

    def boom(module_name, argv):
        raise LLMUnavailable("LLM backend unavailable: https://x.invalid — HTTP 402")

    monkeypatch.setattr(cli, "_delegate", boom)
    monkeypatch.setattr(sys, "argv", ["nimo", "pipeline", "--apk", "x.apk"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == cli.EXIT_LLM_UNAVAILABLE == 3
    err = capsys.readouterr().err
    assert "LLM backend unavailable" in err
    assert "Traceback" not in err, "the traceback must not reach the user"


def test_run_pipeline_distinguishes_a_backend_outage():
    """The customer note must not read as a result for their app.

    A bare "did not produce a report" on a provider outage implies something
    about the app under test. It must say the outage was ours.
    """
    src = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "backend_unavailable" in src, "exit 3 must map to a distinct verdict"
    assert "not a result for your" in src, "the note must disclaim an app verdict"
    assert "status == 3" in src, "the script must branch on the LLM exit code"


def test_run_pipeline_probes_the_backend_before_the_long_run():
    """A sustained outage must not be discovered only after boot.

    Booting the emulator is the slow, expensive part. Finding out afterwards
    that the backend is down wastes it and returns nothing, which is what
    happened on on-demand runs #24 and #25.
    """
    src = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    probe = src.index("llm preflight")
    run = src.index('nimo pipeline "${ARGS[@]}"')
    assert probe < run, "the backend probe must precede the pipeline run"
    assert "NIMO_BACKEND=null" in src, "no deterministic fallback on probe failure"
    assert "DEGRADED=1" in src


def test_degraded_run_is_labelled_not_silent():
    """An unlabelled degraded run is a quieter false green.

    Random exploration finding nothing is much weaker evidence than guided
    exploration finding nothing, and the customer must be told which they got.
    """
    src = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert '"ai_guidance"' in src and "False" in src
    assert "explored " in src and "deterministically" in src, (
        "the note must say the exploration was unguided"
    )
    assert "Crash detection was" in src, (
        "the note must preserve the real guarantee: observed crashes stay real"
    )

    page = (REPO / "docs" / "run.html").read_text(encoding="utf-8")
    assert "ai_guidance === false" in page, "the page must flag a degraded run"
    assert "backend_unavailable" in page, "the page must label an outage distinctly"


def test_backend_degrades_mid_run_instead_of_aborting(monkeypatch):
    """An outage after boot must not throw away the whole run.

    Observed in CI on 2026-10-05: the preflight probe succeeded and the
    pipeline's first real call seconds later returned 402 and stayed 402.
    Booting is the expensive part, so finishing unguided beats returning
    nothing.
    """
    from nimo.llm import ResilientBackend

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(402, "Payment Required")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)

    b = ResilientBackend(LLMClient(base_url="https://x.invalid", api_key="k",
                                   retries=1, backoff=0))
    assert b.degraded is False
    first = json.loads(b.chat([{"role": "user", "content": "hi"}]))
    assert first["action"] == "back", "must serve a usable action, not raise"
    assert b.degraded is True
    assert b.model == "null", "a degraded backend must report itself as null"

    before = calls["n"]
    for _ in range(5):
        b.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == before, "must stop hammering a dead backend"


def test_degradation_is_recorded_process_wide(monkeypatch):
    """repro builds its own backend; the report must still know.

    `pipeline.main` and `repro.reproduce` each construct a backend, so an
    outage hit inside repro is invisible to the pipeline's own instance.
    """
    import nimo.llm as llm_mod

    monkeypatch.setitem(llm_mod._DEGRADED, "hit", False)
    monkeypatch.setitem(llm_mod._DEGRADED, "reason", "")
    monkeypatch.setattr("time.sleep", lambda s: None)

    def fake_urlopen(req, timeout=None):
        raise _http_error(503, "Service Unavailable")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert llm_mod.degradation_occurred() is False
    b = llm_mod.ResilientBackend(
        LLMClient(base_url="https://x.invalid", retries=0, backoff=0))
    b.chat([{"role": "user", "content": "hi"}])
    assert llm_mod.degradation_occurred() is True
    assert "503" in llm_mod.degradation_reason()


def test_degraded_run_cannot_invent_a_bug():
    """The honesty invariant must survive degradation.

    A degraded run loses navigation, not integrity: the null backend only
    ever emits an inert action, so no crash can originate from it.
    """
    from nimo.llm import NullBackend

    out = json.loads(NullBackend().chat([{"role": "user", "content": "x"}]))
    assert out == {"action": "back"}, "the null backend must stay inert"
    assert "crash" not in json.dumps(out).lower()


def test_fallback_can_be_disabled(monkeypatch):
    """Some operators would rather retry than get an unguided run."""
    from nimo.llm import ResilientBackend, get_backend

    monkeypatch.setenv("NIMO_NO_FALLBACK", "1")
    assert not isinstance(get_backend(), ResilientBackend)
    monkeypatch.delenv("NIMO_NO_FALLBACK")
    assert isinstance(get_backend(), ResilientBackend)


def test_report_states_whether_guidance_survived():
    """The report must be checked at the end, not at construction."""
    src = (REPO / "src" / "nimo" / "engine" / "pipeline.py").read_text(encoding="utf-8")
    assert 'report["ai_guidance"] = not degradation_occurred()' in src
    sh = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "ai_guidance" in sh and "pipeline_report.json" in sh, (
        "the harness must read the degradation the pipeline actually hit, "
        "not only its own up-front probe"
    )


def test_null_backend_needs_no_network():
    b = get_backend("null")
    assert json.loads(b.chat([{"role": "user", "content": "hi"}]))["action"] == "back"


def test_run_pipeline_propagates_the_pipeline_exit_code():
    """The Run button must not show a green check for a run that died.

    redroid-test #23 reported success while its log said `pipeline exit: 1`,
    because the script ended on `git push || true`.
    """
    sh = (REPO / "scripts" / "run_pipeline.sh").read_text()
    assert 'STATUS=${PIPESTATUS[0]}' in sh, "must capture nimo's status, not tee's"
    body = sh.rstrip().splitlines()
    assert body[-1].strip() == 'exit "$STATUS"', (
        "run_pipeline.sh must end by propagating the pipeline's exit code; "
        f"last line is {body[-1]!r}"
    )
