"""nimo — the AI QA engineer for Android.

Reproduce reported bugs, discover new crashes autonomously, drive real
gestures a human tester would, and prove how much of the app was exercised —
from just an APK. No SDK, no instrumentation.

This package fuses three code lines:
  * nimo        — the agent loop, pipeline, triage, coverage denominator, auth
  * CARBON      — gesture executor + dual oracle + legitimacy audit
  * VALOR-Droid — coverage-driven exploration + JaCoCo campaigns

The honesty guarantee: a `reproduced` verdict is only ever produced by the
oracle on a real `FATAL EXCEPTION`/ANR in logcat — never from LLM text.
See `nimo.engine.oracle` and `tests/test_honesty.py`.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
