"""Preflight: refuse bad APKs before spending a single device-minute.

Pure static analysis (androguard) — no device needed, so this runs on the
CI runner *before* the emulator/redroid boots. Exit 0 = go, exit 2 = refused.

Usage:
    python -m agent.preflight app.apk [--package com.example.app] [--coverage]
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import dataclass, field

# redroid image tag -> Android API level (mirrors the workflow's mapping)
VERSION_API: dict[str, int] = {
    "16.0.0": 35, "15.0.0": 35, "14.0.0": 34, "13.0.0": 33,
    "12.0.0": 32, "11.0.0": 30, "10.0.0": 29, "9.0.0": 28, "8.1.0": 27,
}
ARM_ABIS = {"armeabi-v7a", "arm64-v8a", "armeabi"}
X86_ABIS = {"x86", "x86_64"}


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
    max_api = 35
    try:
        from androguard.core.apk import APK
        a = APK(apk_path)
        target = a.get_target_sdk_version()
        min_sdk = a.get_min_sdk_version()
        r.info["target_sdk"] = target
        r.info["min_sdk"] = min_sdk
        if target and int(target) < 23:
            max_api = 33  # Android 14+ refuses to install targetSdk < 23
            r.warnings.append(
                f"targets API {target}: Android 14+ refuses to install "
                f"apps targeting < 23 — use android_version 13 or lower")
        if min_sdk and int(min_sdk) > max_api:
            _fail(r, f"minSdk {min_sdk} but newest installable API is "
                     f"{max_api} — no Android version can run this APK")
        debuggable = str(a.get_attribute_value(
            "application", "debuggable")).lower() == "true"
        r.info["debuggable"] = debuggable
    except Exception:
        pass
    r.info["max_api"] = max_api

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


def recommend_env(apk_path: str) -> dict:
    """Pick the runtime environment from the APK itself.

    Returns {"android_version", "api_level", "arch", "max_api",
             "native_abis", "uses_gms", "reasons"}. The workflow uses this
    when the customer leaves version/arch on "auto" — which is the default.
    """
    reasons: list[str] = []
    rec: dict = {"reasons": reasons}

    zf = zipfile.ZipFile(apk_path)
    names = zf.namelist()
    abis = sorted({n.split("/")[1] for n in names
                   if n.startswith("lib/") and n.count("/") >= 2})
    rec["native_abis"] = abis

    # GMS usage: redroid ships no Play Services, so this changes expectations
    uses_gms = False
    for d in [n for n in names if n.endswith(".dex")][:8]:
        try:
            if b"Lcom/google/android/gms/" in zf.read(d):
                uses_gms = True
                break
        except Exception:
            pass
    rec["uses_gms"] = uses_gms
    if uses_gms:
        reasons.append("app uses Google Play Services — redroid has no GMS; "
                       "expect degraded behavior there, emulator fallback too "
                       "unless a Play Store image is configured")

    # Android version: newest image the APK can actually install on
    pf = check(apk_path)
    if not pf.ok:
        # Never recommend an environment from a failed preflight — a
        # degraded guess (e.g. targetSdk unknown -> newest Android) would
        # send the run into an uninstallable image 10 minutes in.
        raise RuntimeError(f"preflight failed, cannot recommend environment: "
                           f"{'; '.join(pf.failures)}")
    max_api = int(pf.info.get("max_api") or 35)
    min_sdk = pf.info.get("min_sdk")
    rec["max_api"] = max_api
    cands = [(v, api) for v, api in VERSION_API.items() if api <= max_api]
    if min_sdk:
        cands = [(v, api) for v, api in cands if api >= int(min_sdk)]
    if not cands:
        raise RuntimeError(
            f"no Android version satisfies minSdk {min_sdk} "
            f"with max installable API {max_api}")
    cands.sort(key=lambda t: t[1], reverse=True)
    version, api = cands[0]
    rec["android_version"] = version
    rec["api_level"] = api
    reasons.append(f"Android {version} (API {api}): newest image the APK "
                   f"(targetSdk {pf.info.get('target_sdk')}) can install on")

    # Arch: ARM-only native code cannot run on amd64 runners — say so
    # instead of silently running broken tests.
    if not abis:
        arch = "amd64"
        reasons.append("no native libraries — pure Java/Kotlin runs anywhere")
    elif abis and set(abis) & X86_ABIS:
        arch = "amd64"
        reasons.append(f"ships x86 native libs ({', '.join(abis)})")
    else:
        arch = "arm64"
        reasons.append(f"ships ARM-only native libs ({', '.join(abis)}) — "
                       "amd64 runners cannot execute them; needs ARM hardware")
    rec["arch"] = arch
    zf.close()
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo APK preflight check")
    ap.add_argument("apk", help="APK file to check")
    ap.add_argument("--package", default=None, help="expected package name")
    ap.add_argument("--coverage", action="store_true",
                    help="require JaCoCo agent evidence (for --coverage-apk)")
    ap.add_argument("--recommend-env", action="store_true",
                    help="print recommended runtime environment as JSON")
    args = ap.parse_args()

    if args.recommend_env:
        print(json.dumps(recommend_env(args.apk), indent=2))
        return

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
