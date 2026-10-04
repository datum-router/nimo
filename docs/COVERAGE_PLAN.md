# Coverage plan: logins, blockers, and honest numbers

Goal: **maximal measured coverage with zero silent gaps.** Every screen and
every code region is either covered or labeled with the exact reason it isn't.
100% code coverage is not attainable on real apps (dead code, defensive
branches, server-gated paths) — so the target is not a fantasy number, it is
*no uncovered region without a reason.*

This plan folds in every hard lesson from the VALOR-Droid campaign (see
"VALOR-Droid issues → fixes" at the bottom).

## Phase 0 — Measure honestly (baseline)

Before chasing coverage, make the numbers trustworthy.

- [x] **Activity coverage** — already reported (`visited/total`). Keep.
- [x] **Code coverage** — `src/nimo/engine/coverage.py`: pulls `coverage.ec` via
      `run-as` (works on debuggable builds where plain `adb pull` fails),
      converts with the JaCoCo CLI when jar + class files are present,
      otherwise reports the raw `.ec` honestly instead of faking numbers.
      Per-package denominator rule documented (VALOR-Droid lesson).
- [x] **Preflight** — `src/nimo/engine/preflight.py`: refuses corrupt APKs, package
      mismatches, and coverage builds with no JaCoCo agent classes in dex
      (DEX-level evidence, not just a properties file — the exact hole that
      burned an hour per app in VALOR-Droid). Wired into the pipeline
      (fail-fast before device boot), `redroid-test.yml` (before redroid
      boots), and a new emulator-free `preflight` job in `demo.yml`.
      Also warns when `targetSdk < 23` (Android 14+ refuses install).
- [x] **Blocker taxonomy** — `src/nimo/engine/blockers.py`: fixed vocabulary of
      reason codes; every unreachable activity carries one.

## Phase 1 — The login ladder (cheapest rung first)

No universal login solver exists (surveyed 2026-09-23: every maintained tool
needs per-app setup). So: an ordered ladder, each rung tried in turn, each
unreachable screen labeled with how far it got.

| # | Strategy | Status in nimo |
|---|----------|----------------|
| 1 | Credentials from `config/auth.yaml` | ✅ have (`auth.py`) |
| 1b | **nimo AutofillService** — nimo's own `AutofillService` APK installed on every test device, fed vault credentials; OS-level fill of any app's login form (no per-app scripting, no typing flakiness). First mechanism tried for credential logins, falls back to adb typing | ✅ done 2026-09-27 (`android/autofill/` source + prebuilt `nimo-autofill.apk`, `src/nimo/engine/autofill.py`, wired into `auth.attempt` + `pipeline.py`) |
| 2 | Throwaway sign-up (`nimo-<uuid>@example.invalid`) | ✅ have |
| 3 | Google Sign-In via pre-authed snapshot | ✅ have — **gap:** redroid images ship no GMS, so the cloud path needs a Play Store emulator image; document in `GOOGLE_LOGIN.md` |
| 4 | **Per-app login prologue (Maestro YAML)** — `login/<pkg>.yaml` with env-var credential injection, run before the crawl | ✅ mechanics done (`src/nimo/engine/login_prologue.py` + `login/_template.yaml`); per-app YAMLs still to be authored |
| 5 | **`run-as` session injection** — for debuggable/coverage builds, pull `shared_prefs`/DBs from a logged-in instance and push into the fresh device | ✅ done (`src/nimo/engine/session_inject.py` capture/restore) |
| 6 | **Golden snapshots** — pre-authed AVD snapshot cached per app, restored in CI | ⬜ new — needs a real emulator + one manual login per app + CI cache wiring (cannot be built without a device) |
| 7 | **Smali login bypass** — strip auth checks during the repack pass (same transform class as JaCoCo instrumentation); target only apps classified `login-wall` in Phase 2 | ✅ harness done (`src/nimo/engine/login_bypass.py`: apktool decode → apply `bypass/<pkg>/` patches → rebuild → sign); patch *content* is per-app reverse engineering, cannot be generated |
| 8 | Direct activity / deep-link launch as bypass probe — launch post-login activities directly, detect auth redirects | ✅ done — redirects are detected, the activity is NOT counted as visited (this was inflating coverage), and lands on login ⇒ `login-wall` label |
| 9 | Honest unreachable with reason code | ✅ done (`src/nimo/engine/blockers.py`) |

Order matters: rungs 1–3 are zero per-app cost; 4–6 cost one setup per app;
7 is invasive and only for blocker-classified apps; 9 is the floor, never a
silent skip.

## Phase 2 — Blocker audit & routing

- [ ] After each run, classify every unreachable activity with the Phase 0
      taxonomy (static signals: manifest, string resources mentioning
      "captcha"/"otp"; dynamic signals: auth-redirect detection from rung 8,
      WebView-only credential fields).
- [ ] Route each class to its rung: `login-wall` → 4/5/6/7, `server-url`
      (Nextcloud-class) → throwaway docker server with seeded creds,
      `otp`/`captcha` → Phase 4 policy.
- [ ] Never spend LLM calls on `captcha` — classified once, reported always.

## Phase 3 — The coverage loop

- [x] Re-run with the next rung; track **coverage delta per strategy** in
      `pipeline_report.json` (`coverage_attempts` — pipeline + crawler both
      record entries).
- [x] CI regression: `demo.yml` gained an emulator-free `preflight` job —
      all demo APKs must pass static checks on every push. (Coverage-threshold
      CI needs real device runs first; not yet.)
- [x] Tarpit recovery (2026-09-27, `src/nimo/engine/tarpit.py` — the Jev pattern):
      6 consecutive zero-gain screens → one LLM escape consultation
      (tap/back/swipe_up/type/relaunch, abstention falls back to BACK +
      foreground reset), max 5 consults per run. The deterministic crawl
      is the engine; the LLM is a rarely-consulted safety net.
- [ ] Weekly: re-audit the blocker list — a rung that stops working (e.g.
      snapshot incompatible after image update) is a bug, not a mystery.

## Phase 4 — Hard blockers policy (declared, not attempted)

CAPTCHA, real-SMS OTP, biometrics-gated flows: **no tool handles these.**
nimo does not pretend to. They are reported as unreachable with the reason,
and the customer note names exactly what wasn't tested and why. This is a
feature: it is what makes every other number believable.

## VALOR-Droid issues → fixes (all ported)

| # | VALOR-Droid issue | Cost when hit | Fix in nimo |
|---|-------------------|---------------|-------------|
| 1 | Denominator included library probes | Every coverage number wrong | Per-package denominator (Phase 0) |
| 2 | Preflight hole: no agent classes in dex, config file present | 1h burned per app, discovered at teardown | DEX-level instrumentation evidence check (Phase 0) |
| 3 | Missing `jacoco-agent.properties` → tcpserver never opened | 3 apps × 3h, `collected:false` | Verify agent config in coverage builds; fail fast |
| 4 | Login walls blocking inner screens | Largest coverage killer | The login ladder (Phase 1) |
| 5 | No valid APKs (MyExpensesDebug, NewPipeDebug); LFS-pointer APKs (RedReader, WikipediaAlpha) | Wasted runs | APK-validity preflight; refuse with reason |
| 6 | Method universe matched on 0/50 apps (vd-dual) | Incomparable numbers | Byte-verify instrumentation; keep denominator stable across runs |
| 7 | No KVM/binder on the dev host | Campaign blocked | Cloud-first: `redroid-test.yml` with binder probe + emulator fallback |
