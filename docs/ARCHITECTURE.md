# Architecture

nimo is a small, readable agent loop. Four modules, no frameworks.

```
bug report (md) + APK
        |
        v
  agent/repro.py        main loop: install -> act -> observe -> verdict
        |  \
        |   \--> agent/llm.py      OpenAI-compatible chat client
        |                          (Pollinations.ai by default, free, no key)
        |
        \--> agent/device.py     adb primitives: install, launch, uiautomator
                                  dump, tap/swipe/type, logcat crash watch,
                                  screenshots
        |
        \--> agent/prompts.py    system + per-step prompts

        v
  repro_report.json     verdict (reproduced | not_reproduced),
                        step history, crash log excerpt
  screenshots/          one PNG per step + final.png
```

## How a run works
1. **Setup** — `force-stop`, `install -r -g`, `logcat -c`, launch via monkey.
2. **Loop (max 25 steps)** —
   dump UI hierarchy → ask the LLM for one JSON action → execute it over adb
   → screenshot → scan logcat for `FATAL EXCEPTION` in the target package.
3. **Verdict** —
   - *repro mode:* `reproduced` if the LLM declares it done-positive or a
     crash is caught in logcat; otherwise `not_reproduced`. Non-crash bugs are
     verified by UI state (the agent reasons about what it sees).
   - *discover mode (`--discover`):* every crash found is captured with its
     trail and screenshot, the app is relaunched, and the hunt continues —
     up to 5 bugs per run. Report lists them all.
4. **Report** — everything lands in the output dir as JSON + PNGs.

## Design choices
- **Stdlib only.** No SDK to install, no dependency drift; runs anywhere
  Python 3.10+ and adb exist — including CI.
- **LLM is a replaceable endpoint**, not a baked-in vendor. Pollinations is
  the zero-friction default; anything OpenAI-compatible slots in via env.
- **Crash detection is independent of the LLM.** The agent can't hallucinate
  a reproduction: a `reproduced` verdict on a crash bug requires a real
  `FATAL EXCEPTION` in logcat.
- **Demos are tiny.** The five demo APKs total under 2 MB and each targets
  a different bug shape (simple crash, state-dependent crash, ordered
  sequence, environment-dependent crash, non-crash behavior).

## What's next
- Guided discovery goals ("stress the checkout flow") on top of free exploration
- iOS support (same loop, different driver)
- Web dashboard + hosted API: submit APK + report, get back the verdict
- Fix suggestion pass: from crash stack to patch candidate
