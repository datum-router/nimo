"""Preflight: refuse bad APKs before spending a single device-minute.

Pure static analysis (androguard) — no device needed, so this runs on the
CI runner *before* the emulator/redroid boots. Exit 0 = go, exit 2 = refused.

Usage:
    python -m agent.preflight app.apk [--package com.example.app] [--coverage]
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from dataclasses import dataclass, field


@dataclass
class PreflightResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def _fail(r: PreflightResult, msg: str) -> None:
    r.failures.append(msg)
    r.ok = False


def check(apk_path: str, expected_package: str | None = None,
          need_coverage: bool = False) -> PreflightResult:
    r = PreflightResult(ok=True)

    # ---- file level ---------------------------------------------------
    try:
        with open(apk_path, "rb") as f:
            head = f.read(4)
    except OSError as exc:
        _fail(r, f"cannot read APK: {exc}")
        return r
    if head != b"PK\x03\x04":
        _fail(r, "not a ZIP file (APKs are ZIPs) — file is corrupt or not an APK")
        return r

    try:
        zf = zipfile.ZipFile(apk_path)
        names = set(zf.namelist())
    except zipfile.BadZipFile:
        _fail(r, "ZIP central directory unreadable — corrupt APK")
        return r
    if "AndroidManifest.xml" not in names:
        _fail(r, "no AndroidManifest.xml — not an installable APK")
        return r
    dexes = sorted(n for n in names if n == "classes.dex" or
                   (n.startswith("classes") and n.endswith(".dex")))
    if not dexes:
        _fail(r, "no classes*.dex — nothing to execute")
        return r
    r.info["dex_files"] = dexes

    # ---- manifest level (androguard) ----------------------------------
    try:
        from . import apkmeta
        meta = apkmeta.analyze(apk_path)
    except Exception as exc:  # androguard missing or parse blew up
        _fail(r, f"manifest parse failed: {exc}")
        return r
    r.info.update(package=meta.package, version=meta.version,
                  app_name=meta.app_name,
                  activities=len(meta.activities))

    if not meta.package:
        _fail(r, "manifest has no package name")
    if expected_package and meta.package != expected_package:
        _fail(r, f"package mismatch: APK is {meta.package}, "
                 f"expected {expected_package}")
    if not meta.activities:
        r.warnings.append("manifest declares zero activities — "
                          "coverage denominator is empty")

    # target/min SDK: old APKs vs new Android
    try:
        from androguard.core.apk import APK
        a = APK(apk_path)
        target = a.get_target_sdk_version()
        min_sdk = a.get_min_sdk_version()
        r.info["target_sdk"] = target
        r.info["min_sdk"] = min_sdk
        if target and int(target) < 23:
            r.warnings.append(
                f"targets API {target}: Android 14+ refuses to install "
                f"apps targeting < 23 — use android_version 13 or lower")
        debuggable = str(a.get_attribute_value(
            "application", "debuggable")).lower() == "true"
        r.info["debuggable"] = debuggable
    except Exception:
        pass

    # ---- coverage build evidence (the VALOR-Droid preflight lesson) ----
    if need_coverage:
        # A jacoco-agent.properties alone never opened the tcpserver; the
        # agent classes must be *in the dex*. Check both.
        has_props = "assets/jacoco-agent.properties" in names or any(
            n.endswith("jacoco-agent.properties") for n in names)
        has_agent_classes = any(n.startswith("org/jacoco/agent/") for n in names)
        has_jacoco_dex = False
        if not has_agent_classes:
            for d in dexes[:8]:  # bounded: big apps have many dex files
                try:
                    if b"jacoco" in zf.read(d).lower():
                        has_jacoco_dex = True
                        break
                except Exception:
                    pass
        r.info["jacoco"] = {"properties": has_props,
                            "agent_classes": has_agent_classes,
                            "dex_evidence": has_jacoco_dex}
        if not (has_agent_classes or has_jacoco_dex):
            _fail(r, "coverage build requested but no JaCoCo agent evidence "
                     "in dex — the tcpserver would never open and the run "
                     "would burn its whole budget (see docs/COVERAGE.md)")
        if not r.info.get("debuggable"):
            r.warnings.append("coverage build is not debuggable — "
                              "coverage.ec pull via run-as will fail; "
                              "the agent must write it to a world-readable path")

    zf.close()
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo APK preflight check")
    ap.add_argument("apk", help="APK file to check")
    ap.add_argument("--package", default=None, help="expected package name")
    ap.add_argument("--coverage", action="store_true",
                    help="require JaCoCo agent evidence (for --coverage-apk)")
    args = ap.parse_args()

    r = check(args.apk, expected_package=args.package,
              need_coverage=args.coverage)
    print(f"[preflight] {args.apk}: {'GO' if r.ok else 'REFUSED'}")
    for k, v in r.info.items():
        print(f"[preflight]   {k}: {v}")
    for w in r.warnings:
        print(f"[preflight]   warning: {w}")
    for f in r.failures:
        print(f"[preflight]   FAIL: {f}")
    sys.exit(0 if r.ok else 2)


if __name__ == "__main__":
    main()
