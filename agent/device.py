"""Android device control over adb. Stdlib only.

Expects `adb` on PATH and exactly one device/emulator connected
(or ANDROID_SERIAL set). Provides the primitives the repro agent needs:
install/launch apps, dump the UI hierarchy, tap/swipe/type, and watch
logcat for crashes in the target package.
"""
from __future__ import annotations

import os
import re
import shutil
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

ADB = shutil.which("adb") or "adb"


class DeviceError(RuntimeError):
    pass


def _run(*args: str, timeout: int = 60) -> str:
    try:
        p = subprocess.run(
            [ADB, *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise DeviceError("adb not found on PATH") from exc
    if p.returncode != 0:
        raise DeviceError(f"adb {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def check_connected() -> str:
    out = _run("devices")
    serials = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    if not serials:
        raise DeviceError("no Android device/emulator connected")
    serial = os.environ.get("ANDROID_SERIAL", serials[0])
    if serial not in serials:
        raise DeviceError(f"ANDROID_SERIAL={serial} not connected")
    return serial


def install(apk_path: str) -> None:
    _run("install", "-r", "-g", apk_path, timeout=180)


def launch(package: str) -> None:
    _run("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")


def force_stop(package: str) -> None:
    _run("shell", "am", "force-stop", package)


def clear_logcat() -> None:
    _run("logcat", "-c")


def tap(x: int, y: int) -> None:
    _run("shell", "input", "tap", str(x), str(y))


def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
    _run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))


def input_text(text: str) -> None:
    # %s escapes spaces for `input text`
    _run("shell", "input", "text", text.replace(" ", "%s"))


def press_back() -> None:
    _run("shell", "input", "keyevent", "4")  # KEYCODE_BACK


def screenshot(path: str) -> None:
    p = subprocess.run([ADB, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=30)
    if p.returncode != 0:
        raise DeviceError("screencap failed")
    png = p.stdout.replace(b"\r\n", b"\n")
    with open(path, "wb") as f:
        f.write(png)


def dump_ui() -> list[dict]:
    """Return visible UI elements as [{text, id, class, bounds, clickable}]."""
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        xml_path = tmp.name
    _run("shell", "uiautomator", "dump", "/sdcard/nimo_ui.xml", timeout=30)
    _run("pull", "/sdcard/nimo_ui.xml", xml_path, timeout=30)
    try:
        tree = ET.parse(xml_path)
    finally:
        os.unlink(xml_path)
    elems: list[dict] = []
    for node in tree.iter("node"):
        text = node.get("text", "") or ""
        desc = node.get("content-desc", "") or ""
        res_id = node.get("resource-id", "") or ""
        cls = node.get("class", "") or ""
        if not (text or desc or res_id):
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
        cx = cy = 0
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        elems.append({
            "label": text or desc or res_id.split("/")[-1],
            "resource_id": res_id,
            "class": cls.split(".")[-1],
            "x": cx,
            "y": cy,
            "clickable": node.get("clickable") == "true",
        })
    return elems


def recent_crash(package: str, since_s: float | None = None) -> str | None:
    """Return the latest FATAL EXCEPTION block for `package`, or None."""
    out = _run("logcat", "-d", "*:E", timeout=30)
    blocks = re.findall(
        r"E AndroidRuntime: FATAL EXCEPTION.*?(?=\n\S|\Z)", out, re.S
    )
    for block in reversed(blocks):
        if package.split(".")[0] in block or package in block:
            return block.strip()[:2000]
    # fallback: any fatal exception if it mentions our package anywhere near
    for block in reversed(blocks):
        if package in block:
            return block.strip()[:2000]
    return None


def wait(s: float = 1.0) -> None:
    time.sleep(s)


def start_activity(component: str) -> None:
    """Launch an activity directly, e.g. 'com.pkg/.MainActivity'.

    This is how the crawler reaches deep screens without navigating the UI.
    """
    _run("shell", "am", "start", "-n", component)


def start_deep_link(uri: str) -> None:
    """Fire a VIEW intent for a deep-link URI from the manifest."""
    _run("shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-d", uri)


def current_activity() -> str | None:
    """Return the focused activity component 'pkg/.Activity', if any."""
    out = _run("shell", "dumpsys", "activity", "activities")
    m = re.search(r"mFocusedApp=ActivityRecord\{[^}]*\s(\S+/\S+)", out)
    if m:
        return m.group(1)
    m = re.search(r"mCurrentFocus=Window\{[^}]*\s(\S+/\S+)", out)
    return m.group(1) if m else None


def current_package() -> str | None:
    act = current_activity()
    return act.split("/")[0] if act else None


def grant_permissions(package: str, permissions: list[str]) -> None:
    """Grant install-time permissions so dialogs don't block the crawl."""
    for p in permissions:
        try:
            _run("shell", "pm", "grant", package, p)
        except DeviceError:
            pass  # not all permissions are grantable; the crawler dismisses the rest


def send_sms(code: str) -> None:
    """Deliver an SMS to the emulator (for OTP screens on emulators)."""
    _run("emu", "sms", "send", "5550100", code)


def pull_file(remote: str, local: str) -> bool:
    """Best-effort `adb pull`. Returns False instead of raising."""
    try:
        _run("pull", remote, local)
        return True
    except DeviceError:
        return False
