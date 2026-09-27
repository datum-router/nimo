# Demo 04 — Droid Comic Viewer: crash without Google Play

**App:** Droid Comic Viewer (`net.androidcomics.acv`), 475 KB
**Type:** crash
**Source bug report:**

> **Title:** Crash on phones without Google Play
>
> Steps to reproduce:
> 1. Open Menu
> 2. Select Settings
> 3. Select About
> 4. Select Google Play listing
>
> On a phone without Google Play Store, this would cause a crash. You may
> catch the exception and use a web URL to show the product on Google Play
> instead. On phones sold on some markets, Google services are not present,
> so this may be a serious problem.

## Expected behavior
Tapping "Google Play listing" opens the store page in a browser (or shows a
message) on devices without the Play Store.

## Observed behavior
Uncaught exception → crash, because the code assumes the Play Store app is
installed.

## Run it
```bash
python -m agent.repro --apk demo/04-comicviewer/app.apk \
    --package net.androidcomics.acv \
    --bug demo/04-comicviewer/bug_report.md \
    --out out/04-comicviewer/
```

Note: run this on an emulator image **without** Google Play services to hit
the crash path — exactly the environment-dependent bug nimo is built for.
