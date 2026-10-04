"""Rung 5 of the login ladder: session-file injection via `adb shell run-as`.

Works on debuggable builds (JaCoCo coverage builds are debuggable by
construction). The trick: log in once on any device, `capture()` the app's
`shared_prefs`/`databases` (where tokens and session flags live), then
`restore()` them into every fresh test device before the crawl. No UI
scripting, no per-run login.

Usage:
    python -m nimo.engine.session_inject capture com.example.app /tmp/sess
    python -m nimo.engine.session_inject restore com.example.app /tmp/sess
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ADB = os.environ.get("ADB", "adb")
SESSION_DIRS = ("shared_prefs", "databases", "files")


def adb_bytes(*args: str, input_data: bytes | None = None,
               timeout: int = 120) -> bytes:
    p = subprocess.run([ADB, *args], input=input_data, capture_output=True,
                       timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: "
                           f"{p.stderr.decode(errors='replace')[:200]}")
    return p.stdout


def capture(package: str, dest_dir: str) -> str:
    """Tar the app's session dirs off the device via run-as. Returns tgz path."""
    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, f"{package}-session.tgz")
    # exec-out gives us the raw tar stream; run-as lets us read the app's
    # private files because the build is debuggable.
    tar_stream = adb_bytes(
        "exec-out", "run-as", package, "tar", "cz",
        "-C", f"/data/data/{package}", *SESSION_DIRS)
    if len(tar_stream) < 100:
        raise RuntimeError("run-as capture produced almost nothing — "
                           "is the app debuggable and logged in?")
    with open(out, "wb") as f:
        f.write(tar_stream)
    print(f"[session] captured {len(tar_stream)} bytes -> {out}")
    return out


def restore(package: str, src_dir: str) -> None:
    """Push a captured session into the device and unpack it via run-as."""
    tgz = os.path.join(src_dir, f"{package}-session.tgz")
    if not os.path.isfile(tgz):
        raise RuntimeError(f"no captured session at {tgz} — run capture first")
    remote = "/data/local/tmp/nimo-session.tgz"
    adb_bytes("push", tgz, remote)
    adb_bytes("shell", "run-as", package, "tar", "xz",
               "-C", f"/data/data/{package}", "-f", remote)
    adb_bytes("shell", f"am force-stop {package}")
    adb_bytes("shell", f"rm {remote}")
    print(f"[session] restored into {package} (force-stopped to pick it up)")


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo session capture/restore")
    ap.add_argument("action", choices=["capture", "restore"])
    ap.add_argument("package")
    ap.add_argument("dir", help="local directory holding the session tgz")
    args = ap.parse_args()
    try:
        if args.action == "capture":
            capture(args.package, args.dir)
        else:
            restore(args.package, args.dir)
    except RuntimeError as exc:
        print(f"[session] FAILED: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
