# Code coverage via VALOR-Droid

Screen coverage ("visited 21/23 activities") is good. **Code coverage**
("executed 73% of methods") is better — and nimo gets it from the
VALOR-Droid JaCoCo toolchain instead of reinventing it.

## How it plugs in

```
 stock app.apk
      │  VALOR-Droid: patch_jacoco + smali-logcat toolchain
      ▼
 app-jacoco.apk          (JaCoCo agent embedded, writes coverage.ec)
      │  nimo pipeline --coverage-apk app-jacoco.apk
      ▼
 crawl runs against the instrumented build
      │  pipeline pulls /data/data/<pkg>/files/coverage.ec
      ▼
 pipeline_report.json → discovery.coverage_ec = "crawl/coverage.ec"
```

Generate the HTML/XML report offline with the JaCoCo CLI against the
app's classes, exactly as the VALOR-Droid campaign does.

## Steps

1. Build the instrumented APK with the VALOR-Droid toolchain
   (`patch_jacoco` + smali repackaging; the same flow as the
   `campaign/*` branches). The agent must be configured to dump
   `coverage.ec` to `/data/data/<pkg>/files/coverage.ec` on exit —
   the pipeline force-stops the app before pulling, which flushes it.
2. Run the pipeline:
   ```
   nimo pipeline --apk app.apk \
       --coverage-apk app-jacoco.apk \
       --out out/coverage-run/
   ```
3. Find `out/coverage-run/crawl/coverage.ec` and report it with JaCoCo.

## Notes

- The instrumented APK must be signed (the toolchain handles this).
- Coverage needs a debuggable-or-rooted path to pull `coverage.ec`. On
  CI emulators (`-writable-system` or userdebug builds) `adb pull` works;
  on locked production devices it won't — screen coverage is the
  fallback and is always reported.
- Coverage denominator discipline is VALOR-Droid's: identical probe
  denominators across runs, so numbers are comparable run to run.
