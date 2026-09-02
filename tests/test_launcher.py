"""The launcher. Nested-session marker scrubbing is enforced here, and a regression would be silent, so the pane script is asserted byte for byte.

`C:\\wt.exe`, `C:\\pwsh.exe` and `C:\\claude.exe` throughout are the table the autouse `_no_real_panes` fixture in `conftest.py` answers `shutil.which` from, so nothing here depends on what is installed on the machine running the suite.
"""

import base64
from pathlib import Path

import pytest

from claude_code_workspaces import launcher
from claude_code_workspaces.launcher import WindowsTerminalLauncher
from claude_code_workspaces.restore import RestoreEntry


def entry(session_id: str, cwd: Path, root: Path) -> RestoreEntry:
    return RestoreEntry(session_id=session_id, cwd=cwd, root=root, agent_name=None, title=None, source="snapshot", last_active=None, transcript=cwd / "t.jsonl")


def payloads(argv: list[str]) -> list[str]:
    return [base64.b64decode(argv[index + 1]).decode("utf-16-le") for index, token in enumerate(argv) if token == "-EncodedCommand"]


def test_a_project_pairs_its_sessions_and_overflows_into_another_tab() -> None:
    """Two panes to a tab is the readable limit; a third session opens a second tab for the same project rather than shrinking the first."""
    api, worker = Path(r"C:\code\api"), Path(r"C:\code\worker")
    groups = {api: [entry("s1", api, api), entry("s2", api, api), entry("s3", api, api)], worker: [entry("s4", worker, worker)]}

    argv = WindowsTerminalLauncher().build(groups)

    assert argv[:3] == [r"C:\wt.exe", "-w", "new"]
    assert argv.count("new-tab") == 3  # api twice, worker once
    assert argv.count("split-pane") == 1
    assert argv.count(";") == 3  # four panes, three separators
    assert len(payloads(argv)) == 4


def test_the_overflow_tab_carries_the_same_project_title() -> None:
    # Forward slashes on purpose: the title comes from `root.name`, and a backslash is an ordinary character in a POSIX path, so `Path(r"C:\code\app").name` is the whole string on Linux and `app` only on Windows. Written this way both platforms see the same three parts.
    root = Path("C:/code/app")
    groups = {root: [entry(f"s{index}", root, root) for index in range(3)]}

    argv = WindowsTerminalLauncher().build(groups)

    assert [argv[index + 1] for index, token in enumerate(argv) if token == "new-tab"] == ["--title", "--title"]
    assert [argv[index + 2] for index, token in enumerate(argv) if token == "new-tab"] == ["app", "app"]


def test_a_pair_sits_side_by_side() -> None:
    root = Path(r"C:\code\app")
    groups = {root: [entry(f"s{index}", root, root) for index in range(4)]}

    argv = WindowsTerminalLauncher().build(groups)

    assert [argv[index + 1] for index, token in enumerate(argv) if token == "split-pane"] == ["-V", "-V"]


def test_a_pane_outlives_the_session_it_opened() -> None:
    """`-EncodedCommand` is a one-shot mode: pwsh exits the moment `claude` does, and Windows Terminal then closes the pane. Measured 2026-08-10 — a pane without `-NoExit` vanished as soon as its command returned, so ending a session took its pane with it and left nothing to resume from."""
    root = Path(r"C:\code\app")

    argv = WindowsTerminalLauncher().build({root: [entry("s1", root, root)]})

    assert argv[-6:-1] == [r"C:\pwsh.exe", "-NoExit", "-NoLogo", "-NoProfile", "-EncodedCommand"]


def test_every_pane_scrubs_the_markers_and_forces_persistence() -> None:
    """Without this a restored session writes no transcript and never registers in `claude agents`."""
    root = Path(r"C:\code\app")

    script = payloads(WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}))[0]

    assert "Where-Object { $_.Name -like 'CLAUDE_*' }" in script
    assert "Remove-Item" in script
    assert "$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'" in script
    # The scrub has to come before anything else, or the marker is still set when claude starts.
    assert script.index("Remove-Item") < script.index("claude.exe")


def test_claude_is_invoked_by_absolute_path() -> None:
    """`wt` panes inherit the system PATH, so a user-PATH install would not be found."""
    root = Path(r"C:\code\app")

    script = payloads(WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}))[0]

    assert "& 'C:\\claude.exe' --resume 's1'" in script


def test_a_quote_in_a_path_cannot_break_out_of_the_script() -> None:
    root = Path(r"C:\code\o'brien")

    script = payloads(WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}))[0]

    assert "Set-Location -LiteralPath 'C:\\code\\o''brien'" in script


def test_each_pane_starts_in_its_own_working_directory() -> None:
    """Set by the pane script, not by `wt -d` — see `test_a_semicolon_in_a_path_cannot_split_the_command_line`."""
    root = Path(r"C:\code\repo")
    nested = root / "service"
    groups = {root: [entry("s1", root, root), entry("s2", nested, root)]}

    argv = WindowsTerminalLauncher().build(groups)

    scripts = payloads(argv)
    assert f"Set-Location -LiteralPath '{root}'" in scripts[0]
    assert f"Set-Location -LiteralPath '{nested}'" in scripts[1]
    assert "-d" not in argv


def test_a_pane_stops_rather_than_resuming_in_the_wrong_directory() -> None:
    """A working directory recorded for a session can be gone by the time the pane runs — a removed worktree, a renamed project, a drive that is not mounted.

    PowerShell's default `$ErrorActionPreference` is `Continue`, so without `-ErrorAction Stop` the pane prints the error, keeps going, and resumes the conversation in whatever directory it happened to start in: the tab title is right, the session really does resume, and Claude Code then reads and writes files in the wrong project. Measured 2026-09-02 with `pwsh -NoProfile` — a script whose first line is a `Set-Location` to a non-existent path stops there with `-ErrorAction Stop` and never reaches the next line, and with `-NoExit` the pane stays open with the message, which names the missing path.

    Deliberately not a filesystem check in Python: the directory can disappear between planning a restore and launching it, so the only test that means anything is the one the pane runs at the moment it runs.
    """
    root = Path(r"C:\code\app")

    lines = payloads(WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}))[0].splitlines()

    assert lines[-2] == f"Set-Location -LiteralPath '{root}' -ErrorAction Stop"
    # The resume is the line after it, so a terminating Set-Location is the only thing standing between a missing directory and a session resumed in the wrong one.
    assert lines[-1].startswith("& 'C:\\claude.exe' --resume ")


def test_the_directory_guard_survives_a_fork() -> None:
    """`--fork-session` appends to the resume line, which is the line the guard protects."""
    root = Path(r"C:\code\app")

    lines = payloads(WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}, fork=True))[0].splitlines()

    assert lines[-2].endswith("-ErrorAction Stop")
    assert lines[-1] == "& 'C:\\claude.exe' --resume 's1' --fork-session"


def test_a_semicolon_in_a_path_cannot_split_the_command_line() -> None:
    """`wt` splits its own command line on `;`, and Python quotes an argv element only when it holds whitespace — so a bare `;` reaches `wt` unquoted and starts a new subcommand. `;` is legal in a Windows directory name.

    The cwd therefore never goes through the argv at all, and the tab title has the character removed.
    """
    root = Path(r"C:\code\a;b")

    argv = WindowsTerminalLauncher().build({root: [entry("s1", root, root)]})

    # One pane needs no separator, so any `;` at all here would be an accidental one.
    assert not any(";" in token for token in argv), f"unquotable separator reached the argv: {[token for token in argv if ';' in token]}"
    # The real path still reaches the pane, escaped by the script rather than by the argv.
    assert f"Set-Location -LiteralPath '{root}'" in payloads(argv)[0]


def test_an_empty_plan_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: started.append(args))

    WindowsTerminalLauncher().launch({})
    WindowsTerminalLauncher().launch({Path(r"C:\code\app"): []})

    assert started == []


def test_a_missing_tool_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None if name == "claude" else "found")

    with pytest.raises(launcher.LauncherUnavailable, match="claude"):
        WindowsTerminalLauncher().build({Path("C:/x"): [entry("s1", Path("C:/x"), Path("C:/x"))]})


def test_the_spawn_environment_is_scrubbed_as_well(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_EFFORT", "high")
    monkeypatch.setenv("PATH", "keep-me")

    environment = launcher.scrubbed_environment()

    assert "CLAUDE_CODE_CHILD_SESSION" not in environment
    assert "CLAUDE_EFFORT" not in environment
    assert environment["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] == "1"
    assert environment["PATH"] == "keep-me"


def test_a_new_window_is_asked_for_by_name() -> None:
    """Nothing in `ccw` asks for one today — every path opens into the window you are in — but the intention still has to map to the right flag."""
    root = Path(r"C:\code\app")

    argv = WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}, window=launcher.NEW_WINDOW)

    assert argv[:3] == [r"C:\wt.exe", "-w", "new"]


def test_a_single_resume_goes_to_the_window_you_are_in() -> None:
    """`last` is the most recently used window, which is the one the key was pressed in."""
    root = Path(r"C:\code\app")

    argv = WindowsTerminalLauncher().build({root: [entry("s1", root, root)]}, window=launcher.CURRENT_WINDOW)

    assert argv[:3] == [r"C:\wt.exe", "-w", "last"]


def test_the_window_choice_reaches_the_spawned_command(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda argv, **_kwargs: started.append(argv))
    root = Path(r"C:\code\app")

    WindowsTerminalLauncher().launch({root: [entry("s1", root, root)]}, window=launcher.CURRENT_WINDOW)

    assert started[0][:3] == [r"C:\wt.exe", "-w", "last"]
