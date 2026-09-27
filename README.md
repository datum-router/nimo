# nimo

**Send a bug report, get back a verified reproduction.**

nimo is an agent that takes a plain-text bug report and an Android APK,
drives the app on a real device or emulator, and returns a verdict —
`reproduced` or `not_reproduced` — with the exact steps, screenshots, and
the crash log. No SDK to integrate, no instrumentation, no account needed.

The LLM backend defaults to [Pollinations.ai](https://pollinations.ai):
free, anonymous, OpenAI-compatible. Swap in any OpenAI-compatible endpoint
(Gemini, OpenAI, Groq, …) with three env vars.

## Try it in 2 minutes

```bash
# needs: python3, adb, one connected device/emulator
python -m agent.repro --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --bug demo/01-notepad/bug_report.md \
    --out out/01-notepad/
```

→ `out/01-notepad/repro_report.json` + step screenshots.

## The five demos

| Demo | App | Bug shape |
|------|-----|-----------|
| [01-notepad](demo/01-notepad/) | Notepad, 40 KB | crash: share note → `FATAL EXCEPTION` |
| [02-atimetracker](demo/02-atimetracker/) | A Time Tracker, 74 KB | crash: restore with no backup |
| [03-asciicam](demo/03-asciicam/) | AsciiCam, 678 KB | crash: ordered steps + rotation mid-flow |
| [04-comicviewer](demo/04-comicviewer/) | Comic Viewer, 475 KB | crash: only on devices without Google Play |
| [05-kiss](demo/05-kiss/) | KISS Launcher, 632 KB | non-crash: favorites bar never appears |

Each demo was chosen because it reproduces in about a minute on an emulator.

## Docs

- [Quickstart](docs/QUICKSTART.md) — setup, all demos, your own LLM, your own app
- [Architecture](docs/ARCHITECTURE.md) — how the agent loop works

## Layout

```
agent/        the repro agent (stdlib only)
config/       LLM config — pollinations.json is the default
demo/         5 tiny APKs + their bug reports
docs/         quickstart + architecture
```

## Status

Early. Android only, agent loop is v1. iOS, hosted API, and fix suggestions
are on the roadmap — see [ARCHITECTURE.md](docs/ARCHITECTURE.md).
