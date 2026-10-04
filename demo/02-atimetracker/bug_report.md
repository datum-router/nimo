# Demo 02 — A Time Tracker: crash on restore

**App:** A Time Tracker (`com.markuspage.android.atimetracker`), 74 KB
**Type:** crash
**Source bug report:**

> **Title:** crash on restore
>
> When trying to restore a backup, the app crashes with "Unfortunately,
> A Time Tracker has stopped." This happens directly after selecting the
> menu item.
>
> Follow-up: the crash happens when the backup file cannot be read — e.g.
> no backup was ever created, or the device has no SD card.

## Steps to reproduce
1. Launch A Time Tracker.
2. Open the menu and select the restore/backup option.
3. The app crashes immediately.

## Expected behavior
The app should check that a backup exists before restoring, and show a
message instead of crashing.

## Observed behavior
`ActivityNotFoundException`-style crash ("has stopped") right after the
menu item is tapped.

## Run it
```bash
nimo repro --apk demo/02-atimetracker/app.apk \
    --package com.markuspage.android.atimetracker \
    --bug demo/02-atimetracker/bug_report.md \
    --out out/02-atimetracker/
```
