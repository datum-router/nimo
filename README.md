# nimo

**The AI QA engineer that can't lie.**

Hand nimo an Android APK. It drives your app like a real tester — taps, types,
swipes, pinches, rotates — and does three things:

1. **Reproduces** the bug from a user's report.
2. **Discovers** new crashes on its own.
3. **Proves** how much of your app it actually exercised.

No SDK. No instrumentation. No code changes to your app.

```bash
pip install nimo
nimo repro app.apk --bug-report bug.md
```

---

## Why it can't hallucinate a bug

This is the part that matters, and it's enforced by code rather than by
marketing copy.

Most LLM-driven testing tools ask the model "did that reproduce the bug?" — so
the model can simply claim success. nimo never asks. A `reproduced` verdict is
produced in exactly one place (`src/nimo/engine/oracle.py`) and requires:

- a **genuine `FATAL EXCEPTION` / ANR in logcat**, parsed from the device — a
  signal the model never touches;
- agreement from a **second, independent signal** (the view-hierarchy state),
  which also detects stuck and looping runs;
- a passing **six-criterion legitimacy audit** — the crash must belong to the
  target package and must follow an action the agent actually took. A failed
  audit downgrades the verdict.

A finding with no crash behind it is recorded as an **observation**, never as a
bug. `Oracle`'s constructor takes a package name and nothing else — it is
structurally incapable of seeing an LLM.

`tests/test_honesty.py` enforces this, including a source-level gate that fails
the build if any module off the oracle path assigns a `reproduced` verdict.

The same rule governs gestures: a multi-touch gesture your device cannot
deliver is reported `UNSUPPORTED`, never quietly downgraded to a single-finger
approximation that "worked".

## What it does

| | |
|---|---|
| **Repro mode** | Reads a bug report, drives the app, returns a verdict anchored to a real crash. |
| **Discover mode** | Explores autonomously, finds crashes nobody reported, dedups them by stack fingerprint. |
| **Gestures** | Long-press, double-tap, rapid-click, drag-and-drop, region/edge swipe, picker-scroll, pinch-zoom, two-finger rotation — the gestures that reproduce bugs tap-only tools structurally cannot. |
| **Coverage** | Two tiers, always labelled: **JaCoCo line coverage** when you supply an instrumented build, **activity (screen) reach** for a plain release APK. |
| **Reports** | One schema → JSON (machine/CI) and HTML (human/PR comment): verdict, bugs with stacks, full gesture trace, coverage, audit result. |

## Install

```bash
pip install nimo                  # engine — stdlib only, no dependencies
pip install "nimo[apk]"           # + androguard, for static APK analysis
pip install "nimo[vision]"        # + Pillow, for annotated screenshots
```

Running against a real app needs `adb` on PATH and one device or emulator
connected (or `ANDROID_SERIAL` set).

## Usage

```bash
nimo repro      app.apk --bug-report bug.md   # reproduce a reported bug
nimo pipeline   app.apk                       # explore + coverage + report
nimo deeplinks  app.apk                       # enumerate manifest deep links
nimo preflight  app.apk                       # is this APK runnable?
nimo summarize  runs/2026-10-04/              # summarize a finished run
nimo selftest                                 # deviceless self-check, no key
```

`nimo selftest` needs no device, no APK and no API key — it verifies the
honesty path, coverage maths, report renderer and gesture surface. It is what
CI runs on every pull request.

## LLM backends

The engine speaks any **OpenAI-compatible** chat-completions endpoint and
defaults to [Pollinations.ai](https://pollinations.ai) — free, no signup, no
API key — so the quickstart works out of the box.

Swap providers with environment variables only; no code changes:

```bash
export NIMO_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
export NIMO_API_KEY=...
export NIMO_MODEL=gemini-2.5-flash
```

There is also a `null` backend that runs the engine deterministically with no
network at all — used for CI and offline demos.

> For anything confidential, choose your backend deliberately: UI structure
> (and, with a vision backend, screenshots) is sent to the configured endpoint.
> The free default is an anonymous service with no data agreement. See
> [SECURITY.md](SECURITY.md).

## How it's built

```
src/nimo/
  cli.py        # command surface
  engine/       # device (adb), gestures, oracle, crawler, coverage, triage, auth
  llm/          # OpenAI-compatible client + swappable backends
  report/       # unified schema + JSON/HTML renderers
  audit/        # legitimacy audit (anti-self-report)
```

nimo fuses three code lines: the **nimo** agent loop and pipeline, **CARBON**'s
gesture executor + dual oracle + legitimacy audit, and **VALOR-Droid**'s
coverage-driven exploration and JaCoCo campaigns. See
[CHANGELOG.md](CHANGELOG.md) for what came from where.

## Benchmarks

Numbers for the fused engine are being re-run on our own harness and will be
published with full methodology. We deliberately do **not** quote the
predecessor projects' published figures as this product's results — see the
honesty rules in [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests run with no device and no API
key:

```bash
pip install -e ".[dev]"
ruff check src tests && pytest -q
```

## License

[Apache-2.0](LICENSE).
