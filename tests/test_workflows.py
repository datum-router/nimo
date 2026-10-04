"""Guards on the GitHub Actions emulator workflows.

Both of these failures cost a full CI round-trip each (~20 min) and produce
confusing symptoms far from their cause, so they are pinned here. Plain-text
parsing keeps this test dependency-free -- the engine itself is stdlib-only
and a YAML parser is not worth adding for two assertions.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
EMULATOR_ACTION = "reactivecircus/android-emulator-runner"


def _workflow_files():
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files, f"no workflow files found under {WORKFLOWS}"
    return files


def _emulator_steps(text):
    """Yield the text of each step block that uses the emulator action."""
    # Steps start at a '- ' list item; split on those at step indentation.
    blocks = re.split(r"\n(?=\s*- (?:name|uses):)", text)
    return [b for b in blocks if EMULATOR_ACTION in b]


def test_emulator_options_keep_no_window():
    """The runners have no display.

    Overriding `emulator-options` REPLACES the action's defaults (which
    include -no-window). Without it, emulator 37.x aborts at startup with
    "no Qt platform plugin could be initialized" and the only visible symptom
    is a boot timeout.
    """
    checked = 0
    for f in _workflow_files():
        for block in _emulator_steps(f.read_text()):
            m = re.search(r"^\s*emulator-options:\s*(.+)$", block, re.M)
            if not m:
                continue  # inheriting the action defaults, which are headless
            checked += 1
            assert "-no-window" in m.group(1), (
                f"{f.name}: emulator-options overrides the defaults but omits "
                f"-no-window, so the emulator will die on the Qt plugin: {m.group(1)!r}"
            )
    assert checked, "expected at least one explicit emulator-options override"


def test_emulator_script_has_no_line_continuations():
    """The action executes `script:` LINE BY LINE.

    A trailing backslash does not join lines, so `nimo repro \\` runs on its
    own and exits with "the following arguments are required".
    """
    checked = 0
    for f in _workflow_files():
        for block in _emulator_steps(f.read_text()):
            m = re.search(r"^(\s*)script:\s*\|?\s*$\n(.*)", block, re.M | re.S)
            if m:
                indent, body = len(m.group(1)), m.group(2)
                lines = []
                for line in body.splitlines():
                    if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                        break  # dedented back out of the script block
                    lines.append(line)
            else:
                m = re.search(r"^\s*script:\s*(.+)$", block, re.M)
                if not m:
                    continue
                lines = [m.group(1)]
            checked += 1
            for line in lines:
                assert not line.rstrip().endswith("\\"), (
                    f"{f.name}: emulator script line ends with a backslash, but "
                    f"this action runs the script line by line, so the command "
                    f"will execute with no arguments: {line.strip()!r}"
                )
    assert checked, "expected at least one emulator script block"
