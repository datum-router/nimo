# Demo 03 — AsciiCam: IndexOutOfBounds on rotate + delete

**App:** AsciiCam (`com.dozingcatsoftware.asciicam`), 678 KB
**Type:** crash
**Source bug report:**

> **Title:** java.lang.IndexOutOfBoundsException: Invalid index 0, size is 0
>
> Reproduce:
> 1. Take a photo and return.
> 2. Click the duplicate icon and select the top-leftmost picture.
>    **Make sure there is only one picture available.**
> 3. Rotate your phone (set auto-rotate on).
> 4. Click `DELETE PICTURE`.

## Expected behavior
The single picture is deleted without error.

## Observed behavior
`java.lang.IndexOutOfBoundsException: Invalid index 0, size is 0` —
the list is empty after rotation but the delete path still indexes into it.

## Run it
```bash
python -m agent.repro --apk demo/03-asciicam/app.apk \
    --package com.dozingcatsoftware.asciicam \
    --bug demo/03-asciicam/bug_report.md \
    --out out/03-asciicam/
```

Note: this demo needs a working camera or a stubbed image source on the
emulator; it exercises nimo's ability to follow a precise multi-step
sequence with device-state changes (rotation) in the middle.
