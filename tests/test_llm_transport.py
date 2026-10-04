"""LLM transport behaviour.

Every case here was observed for real against the live Pollinations endpoint
during an end-to-end CI run, where a single HTTP 500 destroyed a 20-minute
emulator run with a bare urllib traceback.
"""

import io
import json
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


def test_402_is_not_retried_and_explains_itself(monkeypatch):
    """A payment/auth refusal is a config problem.

    Retrying it only burns the emulator's remaining budget, and the message
    must tell the operator what to change.
    """
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(402, "Payment Required")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(LLMUnavailable) as e:
        LLMClient(base_url="https://x.invalid", retries=3, backoff=0).chat(
            [{"role": "user", "content": "hi"}]
        )
    assert calls["n"] == 1, "4xx (other than 429) must not be retried"
    msg = str(e.value)
    assert "402" in msg
    assert "NIMO_BASE_URL" in msg, "the error must name the way out"


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
