# Quickstart

## What you need
- Python 3.10+
- Android SDK platform-tools (`adb` on PATH)
- One connected device or emulator (`adb devices` shows it)
- Internet access (the default LLM, Pollinations.ai, needs no key or signup)

No `pip install` — nimo is standard library only.

## Run a demo
```bash
cd nimo
nimo repro --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --bug demo/01-notepad/bug_report.md \
    --out out/01-notepad/
```

You get `out/01-notepad/repro_report.json` (verdict, steps, crash log) plus
per-step screenshots in `out/01-notepad/screenshots/`.

## Discover bugs with no bug report
```bash
nimo repro --discover \
    --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --out out/discovery/
```

nimo explores the app on its own and reports every crash it finds, each
with its action trail, screenshot, and log.

## The pipeline (recommended)

Free-roam discover is a wander; the pipeline is systematic:

```bash
cp config/auth.yaml.example auth.yaml   # add test creds / google account
nimo pipeline --apk demo/01-notepad/app.apk \
    --auth auth.yaml \
    --out out/pipeline/
```

What happens: manifest parsed → all 7 activities mapped → each launched
directly → every button/field exercised → login walls handled (your creds,
auto sign-up, or Google Sign-In via a pre-authed emulator snapshot —
`docs/GOOGLE_LOGIN.md`) → crashes deduped by stack fingerprint →
`pipeline_report.json` with coverage % and unreachable screens explained.

Add `--bug demo/01-notepad/bug_report.md` to run targeted repro first,
then the full sweep. Add `--coverage-apk app-jacoco.apk` for real
JaCoCo code coverage from the VALOR-Droid toolchain (`docs/COVERAGE.md`).

## Run all five demos
```bash
for d in 01-notepad 02-atimetracker 03-asciicam 04-comicviewer 05-kiss; do
  pkg=$(grep -oP '(?<=`)[a-z][a-z0-9._]*(?=`)' demo/$d/bug_report.md | head -1)
  nimo repro --apk demo/$d/app.apk --package "$pkg" \
      --bug demo/$d/bug_report.md --out out/$d/ || true
done
```

## Use your own LLM
nimo speaks OpenAI-compatible chat APIs. Point it anywhere with env vars:

```bash
export NIMO_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
export NIMO_API_KEY="<your key>"
export NIMO_MODEL="gemini-2.5-flash"
```

Defaults (Pollinations.ai, free, anonymous):
`NIMO_BASE_URL=https://text.pollinations.ai/openai`, `NIMO_MODEL=openai`.

## Your own app + bug
1. Drop the APK anywhere, write the bug report as markdown
   (title, steps, expected vs observed behavior).
2. Find the package name: `aapt dump badging app.apk | grep package`.
3. Run `nimo repro` as above.
