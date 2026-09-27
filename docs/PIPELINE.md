# nimo pipeline

`python -m agent.pipeline` — the full test pass over an APK. Two paths,
one engine.

## Path A — bug report given (targeted)

```
--apk app.apk --bug report.md
```

The classic nimo repro: follow the report, drive the app, return
`reproduced` / `not_reproduced` with steps, screenshots, crash log.
Result lands in `out/repro/`.

## Path B — APK only (systematic discovery)

```
--apk app.apk --auth auth.yaml
```

1. **Map** (`agent/apkmeta.py`) — parse the manifest with androguard:
   every activity, exported flags, intent-filter deep links, permissions.
   This is the coverage denominator.
2. **Install** — `install -r -g`, clear logcat, pre-grant permissions so
   dialogs don't block the crawl.
3. **Crawl** (`agent/crawler.py`) — breadth-first over the map:
   - every activity launched **directly** (`am start -n`), no lucky tap
     sequences needed for deep screens;
   - every deep link fired (`am start -a VIEW -d`);
   - every unvisited element tapped, every field fed an LLM-suggested
     realistic value plus edge-case inputs;
   - state fingerprinting prevents loops; visited (activity, screen)
     pairs are never re-processed.
4. **Login** (`agent/auth.py`) — login walls are classified and handled:
   provided credentials → automatic throwaway sign-up → Google Sign-In
   via the pre-authed emulator snapshot (docs/GOOGLE_LOGIN.md) →
   honest "unreachable + reason" if nothing works.
5. **Triage** (`agent/triage.py`) — every logcat `FATAL EXCEPTION`
   becomes a stack-trace fingerprint; 50 crashes of one bug collapse
   into one entry with occurrence counts.
6. **Report** — `pipeline_report.json`: activities visited / total
   (coverage %), screens, actions, unique bugs with trails and
   screenshots, unreachable activities **with reasons**, auth events.

## Both at once

```
--apk app.apk --bug report.md --auth auth.yaml
```

Runs Path A first, then Path B. You get the verdict on the reported
bug *and* the full discovery sweep in one report.

## Code coverage (optional)

```
--coverage-apk app-jacoco.apk
```

Runs the crawl against a JaCoCo-instrumented build from the
VALOR-Droid toolchain and pulls `coverage.ec`. See docs/COVERAGE.md.

## Budgets

`--max-minutes` (default 30), `--max-actions` (default 400). The crawl
stops at whichever hits first and still writes the full report —
partial coverage with honest numbers beats a timed-out run with none.
