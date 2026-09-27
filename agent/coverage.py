"""Code-coverage collection for --coverage-apk runs.

collect(): pulls coverage.ec off the device after the crawl. JaCoCo
instrumented builds are debuggable, so `run-as` can read the app's private
files even when a plain `adb pull` cannot.

report(): turns coverage.ec into numbers. Full per-package conversion needs
the JaCoCo CLI jar *and* the app's class files; when either is missing we say
so honestly and report what the .ec file itself tells us (its size and the
number of class execution records it carries).
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess

from . import device


def collect(package: str, out_dir: str, remote_path: str | None = None) -> str | None:
    """Pull coverage.ec; returns the local path, or None with a logged reason."""
    remote = remote_path or f"/data/data/{package}/files/coverage.ec"
    local = os.path.join(out_dir, "coverage.ec")
    device.force_stop(package)  # flush: the agent writes the file on shutdown
    device.wait(2.0)
    # plain pull first (works if the agent wrote somewhere readable)…
    if device.pull_file(remote, local) and os.path.getsize(local) > 0:
        print(f"[coverage] pulled {local}")
        return local
    # …then run-as for debuggable builds (the common case).
    try:
        from . import session_inject
        data = session_inject.adb_bytes(
            "exec-out", "run-as", package, "cat", remote)
        if len(data) > 100:
            with open(local, "wb") as f:
                f.write(data)
            print(f"[coverage] pulled via run-as: {local} ({len(data)} bytes)")
            return local
    except Exception as exc:
        print(f"[coverage] run-as pull failed: {exc}")
    print("[coverage] WARNING: no coverage.ec retrieved — is the "
          "instrumented build writing it? see docs/COVERAGE.md")
    return None


def _ec_class_count(ec_path: str) -> int:
    """Count class-execution records in a JaCoCo .ec file (best effort)."""
    try:
        with open(ec_path, "rb") as f:
            blob = f.read()
        # JaCoCo binary format: header 0xC0C0_1111 then blocks;
        # class headers appear as 0x1001. Counting is heuristic.
        return blob.count(b"\x10\x01")
    except OSError:
        return 0


def report(ec_path: str, out_dir: str,
           classes_dir: str | None = None) -> dict:
    """Convert coverage.ec into a report dict. Honest about what is missing."""
    info: dict = {"ec": os.path.basename(ec_path),
                  "ec_bytes": os.path.getsize(ec_path),
                  "ec_class_records": _ec_class_count(ec_path)}
    jar = (os.environ.get("JACOCO_CLI")
           or shutil.which("jacococli.jar") or shutil.which("jacoco-cli"))
    if jar and classes_dir and os.path.isdir(classes_dir):
        xml = os.path.join(out_dir, "coverage.xml")
        cmd = ([shutil.which("java") or "java", "-jar", jar, "report", ec_path,
                "--classfiles", classes_dir, "--xml", xml])
        try:
            subprocess.run(cmd, capture_output=True, timeout=300, check=True)
            info["converted"] = True
            info["xml"] = os.path.basename(xml)
            info["note"] = ("per-package numbers need the app's own classes; "
                            "library classes excluded from the denominator")
        except Exception as exc:
            info["converted"] = False
            info["note"] = f"jacoco CLI failed: {exc}"
    else:
        missing = []
        if not jar:
            missing.append("JaCoCo CLI jar (set JACOCO_CLI)")
        if not classes_dir:
            missing.append("app class files (--classfiles)")
        info["converted"] = False
        info["note"] = ("no per-package conversion — missing: "
                        + ", ".join(missing) + ". Raw .ec kept for later.")
    return info
