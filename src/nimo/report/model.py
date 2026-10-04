"""Unified report schema for the fused engine.

Every run — repro, discover, pipeline — emits one `Report`: the verdict, the
bugs discovered, the full gesture trace, the coverage result (two-tier:
jacoco-line or activity), and the legitimacy-audit result. Renders to JSON
(machine / API) and HTML (human / PR comment).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Verdict = Literal["reproduced", "not_reproduced", "n/a"]
CoverageKind = Literal["jacoco_line", "activity", "none"]


@dataclass
class ApkMeta:
    package: str = ""
    version: str = ""
    activities: int = 0
    path: str = ""


@dataclass
class Action:
    step: int
    kind: str          # tap, type, long_press, pinch, drag, rotate, ...
    target: str = ""   # label / coords
    delivered: bool = True   # False when a gesture was unsupported on-device


@dataclass
class Bug:
    fingerprint: str
    kind: str
    stack: str
    first_seen_step: int = 0
    legitimacy: str = ""   # AuditResult.summary


@dataclass
class CoverageResult:
    kind: CoverageKind = "none"
    percent: float = 0.0
    covered: int = 0
    total: int = 0


@dataclass
class Report:
    apk: ApkMeta
    mode: Literal["repro", "discover", "pipeline"]
    verdict: Verdict = "n/a"
    discovered: list[Bug] = field(default_factory=list)
    gesture_trace: list[Action] = field(default_factory=list)
    coverage: CoverageResult = field(default_factory=CoverageResult)
    legitimacy: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def to_html(self) -> str:
        from .render import render_html
        return render_html(self)
