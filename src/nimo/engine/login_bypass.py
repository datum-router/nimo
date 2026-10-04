"""Rung 7 of the login ladder: smali login-bypass repack harness.

The honest shape of this rung: decoding an APK, editing smali, rebuilding
and signing is fully mechanical — this module does all of that. *What to
edit* (which check to nop out, which flag to force) is per-app reverse
engineering and cannot be generated automatically. Patches live in
`bypass/<package>/`:

    bypass/com.example.app/
        manifest.txt        # one line per patch: "<file> :: <what it does>"
        *.diff             # unified diffs against smali files (applied with `patch`)
        *.py               # patch scripts exposing apply(decoded_dir) -> str note

Needs on PATH: apktool, java, zipalign (Android SDK), apksigner or keytool.
Missing tools fail with a clear message, never half-way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

REQUIRED = ("apktool", "java", "zipalign")


def tools_available() -> dict[str, bool]:
    have = {t: shutil.which(t) is not None for t in REQUIRED}
    have["apksigner"] = shutil.which("apksigner") is not None
    have["keytool"] = shutil.which("keytool") is not None
    have["patch"] = shutil.which("patch") is not None
    return have


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n"
                           f"{(p.stderr or p.stdout)[-800:]}")


def _require_tools() -> None:
    have = tools_available()
    missing = [t for t in REQUIRED if not have[t]]
    if missing:
        raise RuntimeError("login bypass needs missing tools: "
                           + ", ".join(missing))
    if not (have["apksigner"] or have["keytool"]):
        raise RuntimeError("login bypass needs apksigner or keytool for signing")


def decode(apk: str, workdir: str) -> str:
    out = os.path.join(workdir, "decoded")
    _run(["apktool", "d", "-f", "-o", out, apk])
    return out


def apply_patches(decoded_dir: str, patches_dir: str) -> list[str]:
    """Apply *.diff and *.py patches. Returns notes of what was applied."""
    if not os.path.isdir(patches_dir):
        raise RuntimeError(f"no patches at {patches_dir} — nothing to bypass; "
                           "write the per-app smali patches first")
    have = tools_available()
    notes: list[str] = []
    for name in sorted(os.listdir(patches_dir)):
        path = os.path.join(patches_dir, name)
        if name.endswith(".diff"):
            if not have["patch"]:
                raise RuntimeError("`patch` binary missing — cannot apply diffs")
            _run(["patch", "-p1", "-d", decoded_dir, "-i", path])
            notes.append(f"diff {name}")
        elif name.endswith(".py") and name != "__init__.py":
            ns: dict = {}
            with open(path) as f:
                exec(compile(f.read(), path, "exec"), ns)
            if "apply" not in ns:
                raise RuntimeError(f"{name}: no apply(decoded_dir) function")
            notes.append(f"script {name}: {ns['apply'](decoded_dir)}")
    if not notes:
        raise RuntimeError(f"{patches_dir} has no .diff/.py patches")
    return notes


def rebuild(decoded_dir: str, out_apk: str) -> None:
    tmp = out_apk + ".unaligned.apk"
    _run(["apktool", "b", "-o", tmp, decoded_dir])
    _run(["zipalign", "-f", "4", tmp, out_apk])
    os.remove(tmp)


def _debug_keystore(workdir: str) -> str:
    ks = os.path.join(workdir, "nimo-debug.keystore")
    if not os.path.isfile(ks):
        _run(["keytool", "-genkeypair", "-keystore", ks, "-alias", "nimo",
              "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
              "-storepass", "nimo1234", "-keypass", "nimo1234",
              "-dname", "CN=nimo-test"])
    return ks


def sign(apk: str, workdir: str) -> None:
    if shutil.which("apksigner"):
        ks = _debug_keystore(workdir)
        _run(["apksigner", "sign", "--ks", ks, "--ks-pass:pass:nimo1234",
              "--key-pass:pass:nimo1234", "--out", apk + ".signed", apk])
        os.replace(apk + ".signed", apk)
    else:
        raise RuntimeError("apksigner missing and no fallback implemented")


def bypass(apk: str, package: str, out_apk: str,
           bypass_dir: str = "bypass", workdir: str = "/tmp/nimo-bypass") -> dict:
    """Full rung-7 pipeline. Returns a dict for the report's coverage_attempts."""
    _require_tools()
    patches = os.path.join(bypass_dir, package)
    os.makedirs(workdir, exist_ok=True)
    decoded = decode(apk, workdir)
    notes = apply_patches(decoded, patches)
    rebuild(decoded, out_apk)
    sign(out_apk, workdir)
    return {"strategy": "smali-login-bypass", "patches": notes,
            "output": out_apk,
            "note": "debug-signed repack; install with -r -t"}


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: python -m agent.login_bypass app.apk com.pkg out.apk",
              file=sys.stderr)
        sys.exit(2)
    try:
        print(bypass(sys.argv[1], sys.argv[2], sys.argv[3]))
    except RuntimeError as exc:
        print(f"[bypass] FAILED: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
