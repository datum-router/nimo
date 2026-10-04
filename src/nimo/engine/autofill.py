"""Rung 2b of the login ladder: nimo's own AutofillService.

Installs ``com.nimo.autofill`` on the test device, pushes customer
credentials to ``/data/local/tmp/nimo-autofill.json``, and selects it as
the system autofill service. The crawler then taps the username field and
picks the "nimo test login" suggestion the service offers.

Why this instead of typing: values go through the OS autofill contract
(``AutofillValue.forText`` applied directly to the view), so there is no
IME flakiness, no special-character escaping bugs, and it works in WebViews
that expose autofill. No per-app scripting, no root.

``setup()`` is idempotent and runs once per pipeline run. ``attempt_fill()``
is the credentials strategy's first mechanism inside ``auth.attempt``;
on failure it falls back to adb typing.

Security note: credentials live in a world-readable temp path on an
ephemeral CI emulator, wiped when the runner dies. Never used with real
user credentials — test accounts only.
"""

from __future__ import annotations

import json
import os
import tempfile

from . import device

APK_PATH = os.path.join(os.path.dirname(__file__), "autofill", "nimo-autofill.apk")
SERVICE_COMPONENT = "com.nimo.autofill/.NimoAutofillService"
CRED_DEVICE_PATH = "/data/local/tmp/nimo-autofill.json"
DATASET_LABEL = "nimo test login"


def setup(cfg, package: str | None = None) -> tuple[bool, str]:
    """Install + configure the autofill service. Idempotent."""
    if not getattr(cfg, "email", None) or not getattr(cfg, "password", None):
        return False, "no email/password in auth config — autofill not configured"
    if not os.path.isfile(APK_PATH):
        return False, "nimo-autofill.apk missing — run agent/autofill/build.sh"
    try:
        device.install(APK_PATH)
    except Exception as exc:
        return False, f"autofill APK install failed: {exc}"

    creds = {"default": {"username": cfg.email, "password": cfg.password}}
    if package:
        creds[package] = {"username": cfg.email, "password": cfg.password}
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as tmp:
            json.dump(creds, tmp)
            local = tmp.name
        try:
            device._run("push", local, CRED_DEVICE_PATH, timeout=30)
        finally:
            os.unlink(local)
        device._run("shell", "settings", "put", "secure", "autofill_service",
                    SERVICE_COMPONENT, timeout=30)
        current = device._run("shell", "settings", "get", "secure",
                              "autofill_service", timeout=30).strip()
    except Exception as exc:
        return False, f"autofill configuration failed: {exc}"
    if "com.nimo.autofill" not in current:
        return False, f"autofill service not selected (got: {current!r})"
    return True, f"nimo autofill active for {package or 'default'}"


def attempt_fill(elements: list[dict]) -> tuple[bool, str]:
    """Tap username field -> tap the autofill suggestion. Returns (filled, detail).

    Verification (did we actually leave the login screen) stays with the
    caller, same as every other auth strategy.
    """
    field = _find_username_field(elements)
    if not field:
        return False, "no username-like field found"
    try:
        device.tap(field["x"], field["y"])
        device.wait(2.5)  # let the autofill picker appear
        els = device.dump_ui()
        suggestion = None
        for e in els:
            if e["label"] and DATASET_LABEL.lower() in e["label"].lower():
                suggestion = e
                break
        if not suggestion:
            return False, "autofill suggestion did not appear"
        device.tap(suggestion["x"], suggestion["y"])
        device.wait(1.5)
    except Exception as exc:
        return False, f"autofill tap sequence failed: {exc}"
    return True, "autofill suggestion applied"


def _find_username_field(elements: list[dict]) -> dict | None:
    best = None
    best_score = -1
    for e in elements:
        cls = (e.get("class") or "").lower()
        if "edit" not in cls and "text" not in cls:
            continue
        lab = (e.get("label") or "").lower()
        score = 0
        if "email" in lab:
            score = 3
        elif "user" in lab:
            score = 2
        elif "login" in lab or "phone" in lab or "account" in lab:
            score = 1
        if score > best_score:
            best, best_score = e, score
    return best
