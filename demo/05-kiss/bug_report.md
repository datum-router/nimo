# Demo 05 — KISS Launcher: favorites bar won't appear

**App:** KISS Launcher (`fr.neamar.kiss.debug`), 632 KB
**Type:** non-crash (wrong behavior)
**Source bug report:**

> **Title:** Favorites bar doesn't appear under specific UI settings
>
> After updating, I am no longer able to make the shortcut bar appear. The
> cause seems to be the "Hide favorites bar initially" setting. When enabled,
> the favorites bar will not appear when I try to tap on the screen.

## Steps to reproduce
1. Set an app as a favorite.
2. Enable "Show favorites above search bar" / "Hide favorites bar initially"
   in KISS settings.
3. Return to the home screen and tap to reveal the favorites bar.

## Expected behavior
The favorites bar appears when tapped.

## Observed behavior
The favorites bar never appears — the setting makes it permanently hidden.

## Run it
```bash
python -m agent.repro --apk demo/05-kiss/app.apk \
    --package fr.neamar.kiss.debug \
    --bug demo/05-kiss/bug_report.md \
    --out out/05-kiss/
```

Note: this is a *non-crash* bug — no exception in logcat. nimo verifies it
by driving the UI into the reported state and confirming the favorites bar
is absent where the report says it should appear.
