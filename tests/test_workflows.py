"""Guards on the GitHub Actions emulator workflows.

Both of these failures cost a full CI round-trip each (~20 min) and produce
confusing symptoms far from their cause, so they are pinned here. Plain-text
parsing keeps this test dependency-free -- the engine itself is stdlib-only
and a YAML parser is not worth adding for two assertions.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
REPO = Path(__file__).resolve().parents[1]
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


def test_repro_exit_codes_are_distinct():
    """"Did not reproduce" must not share a code with "you invoked me wrong".

    While both were 2, the demo workflow could only choose between failing on
    every legitimate negative result or tolerating 2 and thereby hiding real
    argument errors. Neither is acceptable for a suite whose job is to prove
    the product works.
    """
    from nimo.cli import EXIT_LLM_UNAVAILABLE
    from nimo.engine.repro import EXIT_NOT_REPRODUCED

    codes = {0, 2, EXIT_LLM_UNAVAILABLE, EXIT_NOT_REPRODUCED}
    assert len(codes) == 4, f"exit codes must be mutually distinct: {codes}"
    assert EXIT_NOT_REPRODUCED != 2, "a negative result is not a usage error"

    src = (WORKFLOWS.parents[1] / "src" / "nimo" / "engine" / "repro.py").read_text(
        encoding="utf-8")
    assert "else EXIT_NOT_REPRODUCED" in src


def test_demo_handles_exit_codes_on_one_line():
    """The emulator action runs `script:` line by line.

    A shell variable set on one line is gone by the next, so exit-code
    handling split across lines would read an empty $code and pass
    everything -- silently greening the matrix regardless of outcome. Same
    line-by-line trap that once executed `nimo repro \\` on its own.
    """
    text = (WORKFLOWS / "demo.yml").read_text(encoding="utf-8")
    repro_lines = [ln for ln in text.splitlines() if "nimo repro --apk" in ln]
    assert repro_lines, "no nimo repro invocation found in demo.yml"
    for ln in repro_lines:
        assert "case " in ln and "esac" in ln, (
            "exit-code handling must sit on the SAME line as the invocation; "
            f"offending line: {ln.strip()[:90]}"
        )
        assert "code=0;" in ln, "initialise $code on the same line"
        assert 'exit "$code"' in ln, (
            "an unrecognised exit code must still fail the job"
        )


def test_pipeline_log_lines_are_timestamped():
    """"Why is it slow?" must be answerable from the log alone.

    The log had no clock, so the 170s discovery crawl and the 48s the backend
    spent retrying looked identical to fast steps. Each line now carries
    [mm:ss] since the pipeline started.
    """
    sh = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "%02d:%02d" in sh, "log lines must carry an elapsed-time prefix"
    assert "python3 -u -c" in sh, (
        "the timestamper must be unbuffered, or the live log arrives in "
        "4 KB bursts instead of line by line"
    )
    # Checked against CODE only: the comment above the timestamper names
    # systime()/strftime() to explain why they are not used, and asserting on
    # the raw file would fail on its own documentation.
    code = "\n".join(ln for ln in sh.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "systime(" not in code, (
        "systime()/strftime() are gawk extensions and the Ubuntu runners "
        "ship mawk, where the timestamper would silently emit nothing"
    )

def test_timestamping_does_not_break_the_exit_code_contract():
    """Adding a pipe stage must not cost us the false-green fix.

    redroid-test #23 reported success over a run whose pipeline had died.
    PIPESTATUS[0] still refers to `timeout`/nimo after the timestamper is
    inserted -- but only because it is element ZERO, which is exactly the
    kind of thing a later refactor breaks silently.
    """
    sh = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "STATUS=${PIPESTATUS[0]}" in sh
    line = next(ln for ln in sh.splitlines() if "nimo pipeline" in ln)
    assert line.strip().startswith("timeout "), (
        "nimo must stay the FIRST stage of the pipe, or PIPESTATUS[0] "
        "reports the timestamper's status instead of the pipeline's"
    )


def test_phase_parser_tolerates_the_timestamp_prefix():
    """The live phase label is parsed from the log; the prefix must not blind it."""
    sh = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    assert r"^(?:\[\d+:\d+\] )?\[nimo\]" in sh, (
        "the phase regex must accept an optional timestamp prefix"
    )


def test_live_feed_cadence_matches_the_page():
    """A 20s publish against 6s polling wasted the page's responsiveness."""
    sh = (REPO / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    pusher = sh.split("PUSHER=$!")[0]
    sleeps = [int(m) for m in re.findall(r"sleep (\d+)", pusher)]
    assert sleeps, "the publisher must have a sleep interval"
    assert max(sleeps) <= 10, (
        f"publish interval {max(sleeps)}s is slower than the page's 6s poll"
    )
