"""Rung 4 of the login ladder: per-app scripted login prologue (Maestro YAML).

Convention: `login/<package>.yaml` — a Maestro flow that logs the app in.
Credentials come from the environment (never committed):

    maestro test -e USERNAME="$NIMO_USER" -e PASSWORD="$NIMO_PASS" login/<pkg>.yaml

Runs *before* the crawl. Returns (ran, ok, detail):
  ran=False  — no YAML for this package, or maestro not installed (not an error)
  ran=True   — prologue executed; ok says whether the app left the login screen

Maestro YAML example lives in login/_template.yaml.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from . import auth, device


def find_flow(package: str, login_dir: str = "login") -> str | None:
    cand = os.path.join(login_dir, f"{package}.yaml")
    return cand if os.path.isfile(cand) else None


def run(package: str, cfg: auth.AuthConfig,
        login_dir: str = "login") -> tuple[bool, bool, str]:
    flow = find_flow(package, login_dir)
    if not flow:
        return False, False, f"no login/{package}.yaml — prologue skipped"
    maestro = shutil.which("maestro")
    if not maestro:
        return False, False, "maestro CLI not installed — prologue skipped"
    env = dict(os.environ)
    if cfg.email:
        env["NIMO_USER"] = cfg.email
    if cfg.password:
        env["NIMO_PASS"] = cfg.password
    try:
        p = subprocess.run(
            [maestro, "test", "-e", f"USERNAME={env.get('NIMO_USER', '')}",
             "-e", f"PASSWORD={env.get('NIMO_PASS', '')}", flow],
            capture_output=True, text=True, timeout=300, env=env)
    except Exception as exc:
        return True, False, f"maestro crashed: {exc}"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "")[-500:]
        return True, False, f"maestro flow failed: {tail}"
    # did we actually leave the login screen?
    try:
        els = device.dump_ui()
    except Exception:
        els = []
    if els and auth.looks_like_login(els):
        return True, False, "prologue ran but app still shows a login screen"
    return True, True, f"prologue {os.path.basename(flow)} passed"
