"""Login handling for the pipeline.

Screens that block exploration (login walls) are detected with the LLM and
handled by strategy, in order:

1. **Provided credentials** — email/password from auth.yaml.
2. **Sign-up first** — if the app offers registration, create a throwaway
   account (nimo-<uuid>@example.invalid). Most test builds accept anything.
3. **Google Sign-In** — taps "Sign in with Google", picks the pre-authed
   test account, and accepts the OAuth consent screen. See
   docs/GOOGLE_LOGIN.md: this needs a Play Store emulator image with a test
   Google account added once and snapshotted. After that it is fully
   automatic.
4. **Give up honestly** — OTP-to-real-phone, captchas, biometrics-gated
   flows are recorded as unreachable with the reason, not silently skipped.

Config file (yaml-ish, parsed without dependencies — simple `key: value`):
    email: tester@example.com
    password: s3cret
    google_account: nimo.tester@gmail.com
    allow_signup: true
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from . import device
from .llm import LLMClient

AUTH_CLASSIFY_PROMPT = """You are helping an automated app-testing agent. Look at \
this Android screen and decide whether it is a login / sign-up wall.

UI elements (label | class | x,y | clickable):
{ui_state}

Reply with exactly one JSON object:
{{"login": true/false,
  "kind": "credentials" | "google" | "otp" | "signup_only" | "none",
  "email_field": "<label of email/username field or null>",
  "password_field": "<label of password field or null>",
  "submit": "<label of sign-in button or null>",
  "google_button": "<label of the Google sign-in button or null>",
  "signup_link": "<label of the sign-up / create-account link or null>",
  "note": "<one-line description of what you see>"}}

"kind" is "google" when a Google-branded sign-in button is the primary path. \
"otp" when the screen asks for a code sent by SMS/email."""


@dataclass
class AuthConfig:
    email: str | None = None
    password: str | None = None
    google_account: str | None = None
    allow_signup: bool = True

    @classmethod
    def from_file(cls, path: str | None) -> "AuthConfig":
        cfg: dict[str, str] = {}
        if path:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or ":" not in line:
                        continue
                    k, v = line.split(":", 1)
                    cfg[k.strip()] = v.strip().strip("'\"")
        return cls(
            email=cfg.get("email") or None,
            password=cfg.get("password") or None,
            google_account=cfg.get("google_account") or None,
            allow_signup=cfg.get("allow_signup", "true").lower() != "false",
        )


def classify(elements: list[dict], llm: LLMClient) -> dict:
    lines = []
    for e in elements[:60]:
        lines.append(f"- {e['label']!r} | {e['class']} | "
                     f"({e['x']},{e['y']}) | clickable={e['clickable']}")
    ui_state = "\n".join(lines) or "(empty screen)"
    raw = llm.chat([
        {"role": "system", "content": "Reply with exactly one JSON object, nothing else."},
        {"role": "user", "content": AUTH_CLASSIFY_PROMPT.format(ui_state=ui_state)},
    ])
    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception:
        return {"login": False, "kind": "none", "note": "unparseable"}


def _tap_label(elements: list[dict], label: str | None) -> bool:
    if not label:
        return False
    for e in elements:
        if e["label"] and label.lower() in e["label"].lower() and e["clickable"]:
            device.tap(e["x"], e["y"])
            device.wait(1.5)
            return True
    return False


def _fill_label(elements: list[dict], label: str | None, text: str) -> bool:
    if not label:
        return False
    for e in elements:
        if e["label"] and label.lower() in e["label"].lower():
            device.tap(e["x"], e["y"])
            device.wait(0.8)
            device.input_text(text)
            device.wait(0.5)
            return True
    return False


def _find(elements: list[dict], *needles: str) -> dict | None:
    for e in elements:
        lab = (e["label"] or "").lower()
        if any(n in lab for n in needles) and e["clickable"]:
            return e
    return None


def google_sign_in(elements: list[dict], cfg: AuthConfig,
                   llm: LLMClient, max_steps: int = 8) -> tuple[bool, str]:
    """Tap through Google Sign-In: button -> account chooser -> consent.

    Requires the emulator to already hold the test Google account
    (docs/GOOGLE_LOGIN.md). Returns (success, detail).
    """
    info = classify(elements, llm)
    if not _tap_label(elements, info.get("google_button")):
        btn = _find(elements, "google", "sign in with")
        if not btn:
            return False, "no Google button found"
        device.tap(btn["x"], btn["y"])
        device.wait(2.0)

    for _ in range(max_steps):
        els = device.dump_ui()
        # 1. account chooser: tap our account (or the only one)
        if cfg.google_account:
            hit = None
            for e in els:
                if e["label"] and cfg.google_account.lower() in e["label"].lower() \
                        and e["clickable"]:
                    hit = e
                    break
            if hit:
                device.tap(hit["x"], hit["y"])
                device.wait(2.5)
                continue
        # 2. consent / "Continue as ..." screens
        consent = _find(els, "continue", "allow", "agree", "accept", "next")
        if consent:
            device.tap(consent["x"], consent["y"])
            device.wait(2.5)
            continue
        # 3. check whether we are past auth
        info = classify(els, llm)
        if not info.get("login"):
            return True, "signed in"
        # 4. nothing left to tap -> stuck
        more = _find(els, "sign in")
        if more and more["label"] and "google" in more["label"].lower():
            device.tap(more["x"], more["y"])
            device.wait(2.0)
            continue
        return False, f"stuck at: {info.get('note')}"
    return False, "google flow timed out"


def attempt(elements: list[dict], cfg: AuthConfig, llm: LLMClient) -> tuple[bool, str]:
    """Try to get past a login wall. Returns (passed, detail)."""
    info = classify(elements, llm)
    if not info.get("login"):
        return True, "no login wall"

    kind = info.get("kind")

    # Strategy 1: Google Sign-In (preferred when offered + account configured)
    if kind == "google" and cfg.google_account:
        ok, detail = google_sign_in(elements, cfg, llm)
        if ok:
            return True, "google sign-in"
        # fall through to other strategies on failure

    # Strategy 2: provided credentials
    if kind in ("credentials", "google") and cfg.email and cfg.password:
        _fill_label(elements, info.get("email_field"), cfg.email)
        els = device.dump_ui()
        _fill_label(els, classify(els, llm).get("password_field"), cfg.password)
        els = device.dump_ui()
        _tap_label(els, classify(els, llm).get("submit")) or \
            _tap_label(els, "sign in") or _tap_label(els, "log in")
        device.wait(2.5)
        if not classify(device.dump_ui(), llm).get("login"):
            return True, "credential login"

    # Strategy 3: sign up with a throwaway account
    if cfg.allow_signup:
        els = device.dump_ui()
        info2 = classify(els, llm)
        if _tap_label(els, info2.get("signup_link")) or _find(els, "create account", "sign up", "register"):
            device.wait(2.0)
            fake = f"nimo-{uuid.uuid4().hex[:8]}@example.invalid"
            for _ in range(4):  # fill visible fields, then submit
                els = device.dump_ui()
                filled = False
                for e in els:
                    lab = (e["label"] or "").lower()
                    if "password" in lab and "edit" in e["class"].lower():
                        _fill_label(els, e["label"], "NimoTest123!")
                        filled = True
                    elif any(k in lab for k in ("email", "username")) \
                            and "edit" in e["class"].lower():
                        _fill_label(els, e["label"], fake)
                        filled = True
                sub = _find(els, "create", "sign up", "register", "continue", "next")
                if sub:
                    device.tap(sub["x"], sub["y"])
                    device.wait(2.5)
                if not filled:
                    break
            if not classify(device.dump_ui(), llm).get("login"):
                return True, f"signed up as {fake}"

    # Strategy 4: honest failure
    reasons = {
        "otp": "OTP sent to a real phone number — needs a test number or SMS retrieval",
        "credentials": "no working credentials (auth.yaml) and sign-up unavailable",
        "google": "Google sign-in failed and no fallback worked",
        "signup_only": "sign-up flow could not be completed automatically",
    }
    return False, reasons.get(kind, f"unhandled login kind: {kind}")


def looks_like_login(elements: list[dict]) -> bool:
    """Cheap heuristic pre-check before spending an LLM call."""
    text = " ".join((e["label"] or "") for e in elements).lower()
    return any(k in text for k in (
        "sign in", "log in", "password", "create account", "forgot password",
        "continue with google", "otp", "verification code"))
