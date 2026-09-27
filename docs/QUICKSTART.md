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
python -m agent.repro --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --bug demo/01-notepad/bug_report.md \
    --out out/01-notepad/
```

You get `out/01-notepad/repro_report.json` (verdict, steps, crash log) plus
per-step screenshots in `out/01-notepad/screenshots/`.

## Run all five demos
```bash
for d in 01-notepad 02-atimetracker 03-asciicam 04-comicviewer 05-kiss; do
  pkg=$(grep -oP '(?<=`)[a-z][a-z0-9._]*(?=`)' demo/$d/bug_report.md | head -1)
  python -m agent.repro --apk demo/$d/app.apk --package "$pkg" \
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
3. Run `python -m agent.repro` as above.
