# Demo 01 — Notepad: crash on "Send Note"

**App:** Notepad (`bander.notepad`), 40 KB
**Type:** crash
**Source bug report:**

> **Title:** Long tap on note > send note > crash
>
> The summary pretty much tells you how to reproduce the issue.

## Steps to reproduce
1. Launch the Notepad app.
2. If no notes exist, create one: tap "More options" > "Add Note", type some text, tap "Confirm".
3. On the note list, long-press the note to open the context menu.
4. Tap "Send Note".

## Expected behavior
The Android share sheet opens so the note's content can be sent.

## Observed behavior
The app crashes immediately back to the home screen; a `FATAL EXCEPTION` is recorded in logcat.

## Run it
```bash
python -m agent.repro --apk demo/01-notepad/app.apk \
    --package bander.notepad \
    --bug demo/01-notepad/bug_report.md \
    --out out/01-notepad/
```
