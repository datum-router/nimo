# nimo

**You ship fast. We find what broke.**

People are building apps quicker than ever — with or without AI slop. nimo
is the agent that catches what slipped through, with two modes:

- **Reproduce** — send the bug report your customer gave you plus the APK,
  get back a *verified* reproduction: verdict, step-by-step trace,
  screenshots, crash log.
- **Discover** — just the APK. nimo explores the app like a relentless QA
  engineer, hunts for bugs on its own, and reports everything it finds.

No SDK to integrate, no instrumentation, no account needed. The LLM backend
defaults to [Pollinations.ai](https://pollinations.ai): free, anonymous,
OpenAI-compatible. Swap in any OpenAI-compatible endpoint (Gemini, OpenAI,
Groq, …) with three env vars.

## Try it in 2 minutes

```bash
# needs: python3, adb, one connected device/emulator

# reproduce a reported bug
python -m agent.repro --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --bug demo/01-notepad/bug_report.md \
    --out out/01-notepad/

# ...or just hunt for bugs in any APK
python -m agent.repro --discover --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --out out/discovery/
```

→ `out/*/repro_report.json` + step screenshots.

## Full pipeline: test every corner

```bash
# systematic discovery: static map -> login handling -> BFS crawl -> coverage
python -m agent.pipeline --apk app.apk --auth config/auth.yaml --out out/full/

# bug report + full sweep in one run
python -m agent.pipeline --apk app.apk --bug report.md --auth config/auth.yaml --out out/full/

# with JaCoCo code coverage (instrumented build from the VALOR-Droid toolchain)
python -m agent.pipeline --apk app.apk --coverage-apk app-jacoco.apk --out out/full/
```

The pipeline parses the manifest for every activity and deep link, launches
each directly, crawls every element, gets past login walls (credentials,
auto sign-up, Google Sign-In — see `docs/GOOGLE_LOGIN.md`), dedupes crashes
by stack-trace fingerprint, and reports honest coverage:
`visited 21/23 activities`, unreachable screens listed with reasons.
Details: `docs/PIPELINE.md`.

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
