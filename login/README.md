# Per-app login prologues (Maestro YAML)

Rung 4 of the [login ladder](../docs/COVERAGE_PLAN.md#phase-1--the-login-ladder-cheapest-rung-first):
before the crawl starts, nimo looks for `login/<package>.yaml` and runs it
with Maestro. Credentials come from **environment variables** (`NIMO_USER` /
`NIMO_PASS`, filled from `config/auth.yaml` at runtime) — never commit them.

One file per app, named exactly `<package>.yaml`, e.g. `login/com.example.shop.yaml`.
Copy `_template.yaml` to start. If no file exists for the package, the
prologue is skipped silently and the crawl proceeds to the other rungs.

Maestro docs: https://maestro.mobile.dev
