"""Readability guards on the two published pages.

The theme was rewritten from a dark palette to Amazon's light one. The
failure mode of that kind of change is not a crash -- it is a rule that kept
a light text colour while its background became white, which renders as
invisible text and which no functional test notices.

So the contrast is computed rather than eyeballed: every CSS rule that sets
both a background and a text colour is resolved (through ``var()``
indirection) and checked against the WCAG 2.1 AA threshold. This is a
stronger check than a screenshot and it costs nothing, which matters because
it runs on every commit rather than when someone remembers to look.

Plain-text parsing on purpose -- no CSS parser dependency, and the engine
itself is stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
PAGES = ["index.html", "run.html"]

# WCAG 2.1 AA: 4.5:1 for normal text, 3:1 for large (>=18.66px bold or 24px).
AA_NORMAL = 4.5
AA_LARGE = 3.0


def _parse_hex(value: str) -> tuple[int, int, int] | None:
    v = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        return tuple(int(c * 2, 16) for c in m.group(1))  # type: ignore[return-value]
    m = re.fullmatch(r"#([0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    named = {"white": (255, 255, 255), "black": (0, 0, 0),
             "transparent": None, "inherit": None, "currentcolor": None}
    return named.get(v)


def _variables(css: str) -> dict[str, str]:
    """Collect --name: value declarations from :root blocks."""
    out: dict[str, str] = {}
    for block in re.findall(r":root\s*\{([^}]*)\}", css, re.S):
        for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", block):
            out[name] = val.strip()
    return out


def _resolve(value: str, variables: dict[str, str], depth: int = 0):
    """Resolve a colour, following var() indirection and fallbacks."""
    if depth > 6:
        return None
    value = value.strip()
    m = re.fullmatch(r"var\((--[\w-]+)\s*(?:,\s*(.+))?\)", value)
    if m:
        name, fallback = m.group(1), m.group(2)
        if name in variables:
            return _resolve(variables[name], variables, depth + 1)
        if fallback:
            return _resolve(fallback, variables, depth + 1)
        return None
    # gradients: judge the first colour stop, the lightest/darkest extreme
    # being what text actually sits on at the edge of the element.
    m = re.search(r"(#[0-9a-fA-F]{3,6})", value)
    if value.startswith("linear-gradient") and m:
        return _parse_hex(m.group(1))
    return _parse_hex(value)


def _luminance(rgb: tuple[int, int, int]) -> float:
    def ch(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _rules(css: str):
    """Yield (selector, declarations) for rules declaring colour AND background."""
    for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        decls = dict(
            (k.strip().lower(), v.strip())
            for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)
        )
        bg = decls.get("background") or decls.get("background-color")
        fg = decls.get("color")
        if bg and fg:
            yield sel.strip(), fg, bg, decls


def _font_is_large(decls: dict[str, str]) -> bool:
    m = re.search(r"([\d.]+)px", decls.get("font-size", ""))
    if not m:
        return False
    size = float(m.group(1))
    weight = decls.get("font-weight", "")
    bold = weight in {"bold", "700", "800", "900"}
    return size >= 24 or (bold and size >= 18.66)


@pytest.mark.parametrize("page", PAGES)
def test_every_text_on_background_pair_is_readable(page: str):
    css_blocks = re.findall(
        r"<style>(.*?)</style>",
        (DOCS / page).read_text(encoding="utf-8"), re.S)
    css = "\n".join(css_blocks)
    variables = _variables(css)
    failures = []
    for sel, fg_raw, bg_raw, decls in _rules(css):
        fg = _resolve(fg_raw, variables)
        bg = _resolve(bg_raw, variables)
        if not fg or not bg:
            continue  # transparent / unresolvable: nothing to judge
        ratio = _contrast(fg, bg)
        need = AA_LARGE if _font_is_large(decls) else AA_NORMAL
        if ratio < need:
            failures.append(
                f"{sel}: {fg_raw} on {bg_raw} = {ratio:.2f}:1 (needs {need})")
    assert not failures, (
        f"{page} has unreadable colour pairs:\n  " + "\n  ".join(failures))


@pytest.mark.parametrize("page", PAGES)
def test_text_without_its_own_background_is_readable_on_the_page(page: str):
    """Most text sets only a colour and inherits the surface beneath it.

    The paired check above cannot see those rules, which is the majority of
    them -- a rule that sets ``color`` alone was the single hole that let a
    near-white text colour survive a palette swap unnoticed.

    A colour is judged against every light surface the page actually uses.
    Failing ONE is ambiguous (a rule may only ever appear on a darker inset),
    so only a colour unreadable on ALL of them is reported -- which cannot be
    a false positive: there is then nowhere on the page it could be read.
    """
    css = "\n".join(re.findall(
        r"<style>(.*?)</style>",
        (DOCS / page).read_text(encoding="utf-8"), re.S))
    variables = _variables(css)

    surfaces = []
    for name in ("--bg", "--bg2", "--bg3", "--page", "--card"):
        rgb = _resolve(f"var({name})", variables)
        if rgb and _luminance(rgb) > 0.5:
            surfaces.append((name, rgb))
    surfaces.append(("white", (255, 255, 255)))
    assert surfaces, "no light surfaces found; is this page still light?"

    failures = []
    for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        decls = dict(
            (k.strip().lower(), v.strip())
            for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)
        )
        if "color" not in decls:
            continue
        if decls.get("background") or decls.get("background-color"):
            continue  # covered by the paired check
        # Rules scoped inside a deliberately dark surface are exempt: the
        # nav, the log console and the terminal mock are navy by design.
        flat = sel.strip().lower()
        if any(d in flat for d in ("nav", ".console", ".term", "footer", ".logo")):
            continue
        fg = _resolve(decls["color"], variables)
        if not fg:
            continue
        need = AA_LARGE if _font_is_large(decls) else AA_NORMAL
        ratios = {n: _contrast(fg, bg) for n, bg in surfaces}
        if all(r < need for r in ratios.values()):
            best = max(ratios.values())
            failures.append(
                f"{sel.strip()}: {decls['color']} is unreadable on every "
                f"light surface (best {best:.2f}:1, needs {need})")
    assert not failures, (
        f"{page} has unreadable text colours:\n  " + "\n  ".join(failures))


@pytest.mark.parametrize("page", PAGES)
def test_a_background_is_never_set_without_its_text_colour(page: str):
    """Half a pair is how a theme breaks silently.

    Setting a background and inheriting the text colour works until the
    inherited colour changes -- which is exactly what a palette swap does,
    and it renders as invisible text rather than as an error.

    Scoped to rules that DECLARE a text property, so it flags a surface that
    really draws text and not the two categories that legitimately set only
    a background: containers whose children set the colour (``nav``, the
    console shell) and purely decorative shapes (status dots, progress bars,
    tap ripples), which have no text to be unreadable.
    """
    css = "\n".join(re.findall(
        r"<style>(.*?)</style>",
        (DOCS / page).read_text(encoding="utf-8"), re.S))
    variables = _variables(css)
    text_props = ("font-size", "font-family", "font-weight", "line-height")
    offenders = []
    for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        decls = dict(
            (k.strip().lower(), v.strip())
            for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)
        )
        bg_raw = decls.get("background") or decls.get("background-color")
        if not bg_raw or "color" in decls:
            continue
        if not any(p in decls for p in text_props):
            continue  # decorative or a container; no text of its own
        bg = _resolve(bg_raw, variables)
        if not bg:
            continue
        # A dark surface inheriting dark body text is the dangerous case.
        if _luminance(bg) < 0.25:
            offenders.append(f"{sel.strip()}: background {bg_raw}, no color")
    assert not offenders, (
        f"{page}: dark surfaces that draw text with an inherited colour:\n  "
        + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# Inline styles
# ---------------------------------------------------------------------------
# The checks above parse <style> blocks only, and that gap shipped a real
# bug: the nav's "Run a test" link carried style="color:var(--txt)" on the
# navy bar -- #0F1111 on #232F3E, 1.27:1, invisible. Every stylesheet rule
# passed while the most important call to action could not be read.

DARK_CONTAINERS = ("nav", "footer")


@pytest.mark.parametrize("page", PAGES)
def test_inline_text_colours_inside_dark_chrome_are_readable(page: str):
    html = (DOCS / page).read_text(encoding="utf-8")
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    variables = _variables(css)
    # The nav and footer are navy; resolve their actual background.
    failures = []
    for container in DARK_CONTAINERS:
        m = re.search(rf"<{container}[^>]*>(.*?)</{container}>", html, re.S)
        if not m:
            continue
        rule = re.search(rf"\n\s*{container}\{{([^}}]*)\}}", css)
        bg = None
        if rule:
            decl = dict((k.strip(), v.strip()) for k, v in
                        re.findall(r"([\w-]+)\s*:\s*([^;]+)", rule.group(1)))
            raw = decl.get("background") or decl.get("background-color")
            if raw:
                bg = _resolve(raw, variables)
        if not bg:
            continue
        for style in re.findall(r'style="([^"]*)"', m.group(1)):
            cm = re.search(r"color\s*:\s*([^;]+)", style)
            if not cm:
                continue
            fg = _resolve(cm.group(1), variables)
            if not fg:
                continue
            ratio = _contrast(fg, bg)
            if ratio < AA_NORMAL:
                failures.append(
                    f"<{container}> inline color {cm.group(1).strip()} on "
                    f"{bg} = {ratio:.2f}:1")
    assert not failures, (
        f"{page}: unreadable inline colours inside dark chrome:\n  "
        + "\n  ".join(failures))


@pytest.mark.parametrize("page", PAGES)
def test_no_inline_style_uses_body_text_colour_on_chrome(page: str):
    """Belt and braces: --txt means "text on a light surface".

    Using it inside the navy nav or footer is always wrong, whatever the
    computed ratio happens to be after a palette tweak.
    """
    html = (DOCS / page).read_text(encoding="utf-8")
    for container in DARK_CONTAINERS:
        m = re.search(rf"<{container}[^>]*>(.*?)</{container}>", html, re.S)
        if not m:
            continue
        assert "color:var(--txt)" not in m.group(1).replace(" ", ""), (
            f"{page}: <{container}> uses the light-surface text colour inline"
        )
