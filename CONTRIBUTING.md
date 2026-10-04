# Contributing to nimo

## Setup

The engine is **stdlib-only** — no runtime dependencies. You only need Python
3.10+ and the dev tooling:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Optional extras:

```bash
pip install -e ".[dev,apk]"     # androguard — static APK analysis
pip install -e ".[dev,vision]"  # Pillow — annotated screenshots
```

## Check your work

```bash
ruff check src tests    # lint
pytest -q               # full suite — no device, no LLM key needed
nimo selftest           # deviceless smoke (what CI runs)
```

Running against a real app additionally needs `adb` on PATH and one
device/emulator connected (or `ANDROID_SERIAL` set).

## The one rule you must not break

nimo's product claim is that **the agent cannot hallucinate a bug**. That is
enforced by code, not by good intentions:

1. A `reproduced` verdict comes **only** from `nimo.engine.oracle`, and only
   from a real `FATAL EXCEPTION`/ANR in logcat.
2. `Oracle` never receives an LLM. Its constructor takes a package name and
   nothing else.
3. A discovered finding with no crash is an **observation**, never a `bug`.
4. Every finding carries a legitimacy-audit result; a failed audit downgrades
   the verdict.

`tests/test_honesty.py` enforces all four, including a source-level gate that
fails the build if any module outside the oracle path assigns a `reproduced`
verdict. If that test fails, fix your code — do not edit the test.

The same honesty rule applies to gestures: a multi-touch gesture the device
cannot deliver must report `False` and be marked `UNSUPPORTED` in the report.
Never silently substitute a single-finger approximation.

## Pull requests

- Keep PRs focused; one concern each.
- Add or update tests for behaviour you change.
- `ruff check` and `pytest` must pass.
- Don't commit APKs, run artifacts, screenshots, or `auth.yaml`.

## Project layout

```
src/nimo/
  cli.py            # `nimo repro|pipeline|deeplinks|preflight|summarize|selftest`
  engine/           # the agent: device, gestures, oracle, crawler, coverage, triage
  llm/              # OpenAI-compatible client + backends (default: Pollinations)
  report/           # unified report schema + json/html renderers
  audit/            # legitimacy audit (anti-self-report)
tests/              # deviceless unit + honesty invariants
```
