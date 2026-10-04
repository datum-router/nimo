"""Render a Report to standalone HTML (human view / PR comment body)."""
from __future__ import annotations

import html


def render_html(report) -> str:
    v = report.verdict
    color = {"reproduced": "#16a34a", "not_reproduced": "#d97706",
             "n/a": "#5a6473"}.get(v, "#5a6473")
    rows = "".join(
        f"<tr><td>{a.step}</td><td>{html.escape(a.kind)}</td>"
        f"<td>{html.escape(a.target)}</td>"
        f"<td>{'yes' if a.delivered else 'UNSUPPORTED'}</td></tr>"
        for a in report.gesture_trace
    )
    bugs = "".join(
        f"<li><b>{html.escape(b.kind)}</b> <code>{b.fingerprint}</code> "
        f"— audit {html.escape(b.legitimacy)}<pre>{html.escape(b.stack[:1200])}</pre></li>"
        for b in report.discovered
    ) or "<li>none</li>"
    cov = report.coverage
    return f"""<!doctype html><meta charset=utf-8>
<title>nimo report — {html.escape(report.apk.package)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}}
code,pre{{font-family:ui-monospace,monospace}} pre{{background:#f4f4f6;padding:.5rem;overflow:auto}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #e5e8ee;padding:4px 8px;text-align:left}}
.verdict{{font-size:1.4rem;font-weight:800;color:{color}}}</style>
<h1>nimo report</h1>
<p><b>APK:</b> {html.escape(report.apk.package)} {html.escape(report.apk.version)} ·
<b>mode:</b> {report.mode}</p>
<p class=verdict>Verdict: {v}</p>
<p><b>Coverage:</b> {cov.percent:.1f}% ({cov.kind}, {cov.covered}/{cov.total}) ·
<b>Legitimacy:</b> {html.escape(report.legitimacy)}</p>
<h2>Discovered bugs</h2><ul>{bugs}</ul>
<h2>Gesture trace</h2>
<table><tr><th>#</th><th>action</th><th>target</th><th>delivered</th></tr>{rows}</table>
"""
