# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-10-04

First packaged release: the three code lines fused into one installable
product.

### Added
- **Packaging** — `pyproject.toml` (hatchling), `pip install nimo`, `nimo`
  console entry point, semver. The engine is stdlib-only; `apk` (androguard)
  and `vision` (Pillow) are opt-in extras.
- **`nimo` CLI** — `repro`, `pipeline`, `deeplinks`, `preflight`, `summarize`,
  `selftest`, `version`.
- **`nimo selftest`** — deviceless, network-free smoke check of the honesty
  path, coverage maths, report renderer and gesture surface. This is what CI
  runs on every PR.
- **Dual oracle** (`engine/oracle.py`, from CARBON) — crash signal (logcat
  `FATAL EXCEPTION`/ANR) plus view-hierarchy state signal, with progress and
  stuck detection. The verdict is produced here and nowhere else.
- **Gesture executor** (`engine/gestures.py`, from CARBON) — long-press,
  double-tap, rapid-click, drag-and-drop, region/edge swipe, picker-scroll,
  pinch and two-finger rotation, re-expressed on nimo's stdlib adb layer
  instead of uiautomator2. Multi-touch reports delivery failure rather than
  faking a single-finger approximation.
- **Legitimacy audit** (`audit/legitimacy.py`, from CARBON) — six-criterion
  anti-self-report check; a failed audit downgrades the verdict.
- **Activity coverage** (`engine/coverage.activity_coverage`, VALOR-Droid's
  screen-reach idea) — honest coverage for release APKs with no instrumented
  build, alongside the existing JaCoCo line-coverage path. Every coverage
  number is labelled with its `kind`.
- **Unified report** (`report/`) — one schema carrying verdict, discovered
  bugs, full gesture trace, coverage and audit result; renders to JSON and
  HTML with untrusted app text escaped.
- **Swappable backends** (`llm/`) — `get_backend()`; `null` deterministic
  backend for CI and offline runs. Pollinations.ai remains the free default.
- **Quality gates** — `tests/` (42 tests: honesty invariants, gestures,
  oracle, coverage, report, backends), ruff + GitHub Actions CI with a
  no-key/no-device smoke job.
- **Repo hygiene** — Apache-2.0 `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`
  (untrusted-APK threat model), `CHANGELOG.md`, `.gitignore`.

### Changed
- Restructured to a `src/` layout: `agent/` became `src/nimo/engine/`, and
  `agent/llm.py` became the `src/nimo/llm/` package. History preserved via
  `git mv`.
- `repro.py` long-press now routes through the fused gesture executor.

### Fixed
- Duplicate `re` import in the device layer; several unused imports across the
  ported modules; three multi-statement lines in `repro.py`.
