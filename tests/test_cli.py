"""The command surface. Every failure the user can cause has to be a sentence, never a traceback."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_code_workspaces import cli, launcher, live, workspaces
from conftest import conversation, live_session, make_repo, write_claude_config, write_transcript

runner = CliRunner()


@pytest.fixture
def on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """CliRunner captures stdout, so the human-readable branch is only reachable by saying so."""
    monkeypatch.setattr(cli, "_machine_readable", lambda explicit: explicit)


@pytest.fixture
def project(home: Path, monkeypatch: pytest.MonkeyPatch):
    """A trusted repository with two sessions on disk, and a switch for what is live."""
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    for session_id in ("s1", "s2"):
        write_transcript(home, session_id, root, conversation(session_id, root))

    def set_live(*session_ids: str):
        sessions = [live_session(session_id, root, name=f"app-{session_id}") for session_id in session_ids]
        monkeypatch.setattr(live, "try_live_sessions", lambda: (sessions, None))

    set_live("s1", "s2")
    return root, set_live


def test_save_records_what_is_live(project) -> None:
    result = runner.invoke(cli.app, ["save", "w"])

    assert result.exit_code == 0
    assert workspaces.load("w").session_ids == ["s1", "s2"]


def test_an_unusable_name_is_a_message_not_a_traceback(project) -> None:
    result = runner.invoke(cli.app, ["save", "Bad Name"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "not a usable workspace name" in result.output
    assert "Traceback" not in result.output


def test_saving_over_an_existing_workspace_needs_force(project) -> None:
    runner.invoke(cli.app, ["save", "w"])
    _, set_live = project
    set_live("s1")

    blocked = runner.invoke(cli.app, ["save", "w"])
    assert blocked.exit_code == 1
    assert workspaces.load("w").session_ids == ["s1", "s2"]

    forced = runner.invoke(cli.app, ["save", "w", "--force"])
    assert forced.exit_code == 0
    assert workspaces.load("w").session_ids == ["s1"]


def test_saving_with_nothing_running_is_refused(project) -> None:
    _, set_live = project
    set_live()

    result = runner.invoke(cli.app, ["save", "w"])

    assert result.exit_code == 1
    assert "nothing to save" in result.output


def test_list_reports_every_workspace(project) -> None:
    runner.invoke(cli.app, ["save", "one"])
    runner.invoke(cli.app, ["save", "two"])

    result = runner.invoke(cli.app, ["list", "--json"])

    assert sorted(entry["name"] for entry in json.loads(result.output)) == ["one", "two"]


def test_list_with_nothing_saved_says_how_to_start(project, on_a_terminal) -> None:
    result = runner.invoke(cli.app, ["list"])

    assert result.exit_code == 0
    assert "ccw save" in result.output


def test_output_is_json_automatically_when_stdout_is_not_a_terminal(project) -> None:
    """Piping `ccw list` somewhere should not hand the pipe a table."""
    runner.invoke(cli.app, ["save", "w"])

    result = runner.invoke(cli.app, ["list"])

    assert json.loads(result.output)[0]["name"] == "w"


def test_refresh_replaces_the_contents(project) -> None:
    runner.invoke(cli.app, ["save", "w"])
    _, set_live = project
    set_live("s2")

    result = runner.invoke(cli.app, ["refresh", "w"])

    assert result.exit_code == 0
    assert workspaces.load("w").session_ids == ["s2"]
    assert "1 added, 1 dropped" in result.output or "0 added, 1 dropped" in result.output


def test_refresh_with_nothing_running_refuses_to_empty_the_set(project) -> None:
    runner.invoke(cli.app, ["save", "w"])
    _, set_live = project
    set_live()

    result = runner.invoke(cli.app, ["refresh", "w"])

    assert result.exit_code == 1
    assert workspaces.load("w").session_ids == ["s1", "s2"]


def test_add_accepts_a_session_that_has_finished(project) -> None:
    """`s2` has a transcript and is not running. A member is an id and a cwd, and the transcript carries both."""
    _, set_live = project
    set_live("s1")
    runner.invoke(cli.app, ["save", "w"])

    result = runner.invoke(cli.app, ["add", "w", "s2"])

    assert result.exit_code == 0
    assert workspaces.load("w").session_ids == ["s1", "s2"]


def test_add_refuses_an_id_that_is_neither_running_nor_on_disk(project) -> None:
    _, set_live = project
    set_live("s1")
    runner.invoke(cli.app, ["save", "w"])

    result = runner.invoke(cli.app, ["add", "w", "typo"])

    assert result.exit_code == cli.EXIT_NOT_FOUND
    assert "typo" in result.output
    assert workspaces.load("w").session_ids == ["s1"]


def test_add_extends_the_set(project) -> None:
    _, set_live = project
    set_live("s1")
    runner.invoke(cli.app, ["save", "w"])
    set_live("s1", "s2")

    result = runner.invoke(cli.app, ["add", "w", "s2"])

    assert result.exit_code == 0
    assert workspaces.load("w").session_ids == ["s1", "s2"]


def test_remove_drops_a_member_even_when_it_no_longer_exists(project) -> None:
    runner.invoke(cli.app, ["save", "w"])

    result = runner.invoke(cli.app, ["remove", "w", "s1", "never-there"])

    assert result.exit_code == 0
    assert workspaces.load("w").session_ids == ["s2"]
    assert "never-there" in result.output


def test_rename_keeps_the_contents(project) -> None:
    runner.invoke(cli.app, ["save", "old"])

    result = runner.invoke(cli.app, ["rename", "old", "new"])

    assert result.exit_code == 0
    assert workspaces.load("new").session_ids == ["s1", "s2"]
    assert not workspaces.exists("old")


def test_rm_confirms_before_moving_anything(project, home: Path, on_a_terminal) -> None:
    runner.invoke(cli.app, ["save", "w"])

    declined = runner.invoke(cli.app, ["rm", "w"], input="n\n")
    assert declined.exit_code == cli.EXIT_CANCELLED
    assert workspaces.exists("w")

    accepted = runner.invoke(cli.app, ["rm", "w"], input="y\n")
    assert accepted.exit_code == 0
    assert not workspaces.exists("w")
    assert list((home / ".ccw" / "trash").glob("w.*.json"))


def test_rm_lists_what_it_is_about_to_move(project, on_a_terminal) -> None:
    runner.invoke(cli.app, ["save", "w"])

    result = runner.invoke(cli.app, ["rm", "w"], input="n\n")

    assert "2 session(s)" in result.output
    assert "app-s1" in result.output


def test_rm_refuses_rather_than_prompting_when_there_is_nobody_to_ask(project) -> None:
    """Piped or driven by an agent there is no terminal, so the confirmation would block forever."""
    runner.invoke(cli.app, ["save", "w"])

    result = runner.invoke(cli.app, ["rm", "w"])

    assert result.exit_code == cli.EXIT_NO_TTY
    assert workspaces.exists("w")
    assert json.loads(result.stderr)["error"]["code"] == "no-tty"
    assert "--yes" in json.loads(result.stderr)["error"]["message"]


def test_every_command_that_names_a_missing_workspace_exits_cleanly(project) -> None:
    for argv in (["refresh", "nope"], ["add", "nope", "s1"], ["remove", "nope", "s1"], ["rename", "nope", "other"], ["rm", "nope"], ["untrash", "nope"]):
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == cli.EXIT_NOT_FOUND, argv
        assert "Traceback" not in result.output, argv


def test_a_failure_class_has_an_exit_code_of_its_own(project, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller branches on the code; string-matching a sentence is not a contract."""
    runner.invoke(cli.app, ["save", "w"])

    assert runner.invoke(cli.app, ["save", "Bad Name"]).exit_code == cli.EXIT_REFUSED
    assert runner.invoke(cli.app, ["refresh", "nope"]).exit_code == cli.EXIT_NOT_FOUND

    monkeypatch.setattr(
        "claude_code_workspaces.launcher.WindowsTerminalLauncher.build",
        lambda self, groups, **kwargs: (_ for _ in ()).throw(launcher.LauncherUnavailable("wt.exe is not on PATH.")),
    )
    _, set_live = project
    set_live()
    assert runner.invoke(cli.app, ["restore", "--yes", "--dry-run"]).exit_code == cli.EXIT_UNAVAILABLE


def test_a_failure_in_machine_mode_is_a_json_envelope_not_prose(project) -> None:
    result = runner.invoke(cli.app, ["refresh", "nope"])

    envelope = json.loads(result.stderr)["error"]
    assert envelope["code"] == "not-found"
    assert "nope" in envelope["message"]
    assert envelope["exitCode"] == cli.EXIT_NOT_FOUND
    assert result.stdout == ""


def test_a_failure_on_a_terminal_stays_a_sentence(project, on_a_terminal) -> None:
    result = runner.invoke(cli.app, ["refresh", "nope"])

    assert result.stderr.startswith("No workspace called 'nope'")


def test_progress_never_lands_on_stdout(project, monkeypatch: pytest.MonkeyPatch) -> None:
    """A consumer reads stdout. A JSON document followed by prose is not parseable."""
    monkeypatch.setattr("claude_code_workspaces.launcher.WindowsTerminalLauncher.launch", lambda self, groups, **kwargs: None)
    monkeypatch.setattr(cli.service, "record_restored_set", lambda: None)
    runner.invoke(cli.app, ["save", "w"])
    _, set_live = project
    set_live()

    result = runner.invoke(cli.app, ["restore", "--yes", "--json"])

    assert result.exit_code == 0
    assert sorted(entry["sessionId"] for entry in json.loads(result.stdout)["entries"]) == ["s1", "s2"]
    assert "pane(s)" in result.stderr


def test_the_plan_json_carries_no_display_row(project) -> None:
    """`line` was a fixed-width, truncated checklist row: a TUI detail, and a lossy copy of fields already here."""
    _, set_live = project
    set_live()

    result = runner.invoke(cli.app, ["restore", "--json"])

    entry = json.loads(result.stdout)["entries"][0]
    assert "line" not in entry
    assert entry["label"] and entry["cwd"]


def test_an_empty_plan_exits_the_same_way_whether_or_not_it_is_piped(home: Path, set_live, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to restore is not success, and the answer cannot depend on what stdout happens to be."""
    piped = runner.invoke(cli.app, ["restore", "--yes"])
    assert piped.exit_code == cli.EXIT_NOTHING_TO_DO
    assert json.loads(piped.stdout)["entries"] == []
    assert json.loads(piped.stderr)["error"]["code"] == "nothing"

    monkeypatch.setattr(cli, "_machine_readable", lambda explicit: explicit)
    on_terminal = runner.invoke(cli.app, ["restore", "--yes"])
    assert on_terminal.exit_code == cli.EXIT_NOTHING_TO_DO


def test_the_top_level_help_says_that_bare_ccw_opens_the_list(project) -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert "With no arguments `ccw` opens the session list" in " ".join(result.output.split())

    restore_help = runner.invoke(cli.app, ["restore", "--help"])
    assert "(`w`)" not in restore_help.output, "a keybinding means nothing to someone reading `--help`"


def test_restore_takes_no_workspace_name(project) -> None:
    """Decided 2026-07-31. Restoring a saved set is `w` on the list, because a name is the part nobody remembers and the list already shows them all."""
    result = runner.invoke(cli.app, ["restore", "w"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_restore_is_the_crash_reflex(project) -> None:
    _, set_live = project
    set_live()

    result = runner.invoke(cli.app, ["restore", "--json"])

    payload = json.loads(result.output)
    assert {entry["source"] for entry in payload["entries"]} == {"heuristic"}


def test_a_dry_run_opens_nothing(project, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []
    monkeypatch.setattr(
        "claude_code_workspaces.launcher.WindowsTerminalLauncher.launch",
        lambda self, groups, **kwargs: started.append(groups),
    )
    _, set_live = project
    # Any command holding the live set leaves a snapshot, and a snapshot is what the crash plan ticks by default.
    runner.invoke(cli.app, ["save", "w"])
    set_live()

    result = runner.invoke(cli.app, ["restore", "--dry-run", "--yes"])

    assert started == []
    assert "Would open 2 pane(s)" in result.output


def test_a_store_that_cannot_be_written_is_an_environment_failure_not_a_bad_request(project, home: Path) -> None:
    """A full disk and an unusable name are not the same answer. Told the request was refused, an agent retries under another name forever; told the environment cannot do it, it stops and reports the machine."""
    (home / ".ccw").mkdir(parents=True, exist_ok=True)
    (home / ".ccw" / "workspaces").write_text("a file where the directory has to go", encoding="utf-8")

    result = runner.invoke(cli.app, ["save", "w"])

    assert result.exit_code == cli.EXIT_UNAVAILABLE
    assert result.exit_code != cli.EXIT_REFUSED
    assert json.loads(result.stderr)["error"]["code"] == "unavailable"


def test_a_machine_dry_run_carries_the_command_line_on_stdout(project, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--dry-run` is asked for exactly one thing: the argv. Printed on stderr with the prose, it is the one part of the answer a consumer of stdout never sees."""
    argv = [r"C:\Program Files\WindowsApps\wt.exe", "-d", r"C:\Users\me\My Documents\app"]

    class Spaced:
        name = "Windows Terminal"

        def build(self, groups, *, fork=False, window=launcher.NEW_WINDOW):
            return argv

    monkeypatch.setattr(cli.service, "default_launcher", Spaced)
    _, set_live = project
    # Any command holding the live set leaves a snapshot, and a snapshot is what the crash plan ticks by default.
    runner.invoke(cli.app, ["save", "w"])
    set_live()

    result = runner.invoke(cli.app, ["restore", "--dry-run", "--json"])

    assert json.loads(result.stdout)["commandLine"] == '"C:\\Program Files\\WindowsApps\\wt.exe" -d "C:\\Users\\me\\My Documents\\app"'


def test_the_trash_is_listed_and_can_be_undone(project, on_a_terminal) -> None:
    """`rm` promises the workspace can come back; these are the two commands that make that true."""
    runner.invoke(cli.app, ["save", "w"])
    removed = runner.invoke(cli.app, ["rm", "w", "--yes"])

    assert "ccw untrash w" in removed.output, "the message that promises recovery has to name the command that does it"

    listed = runner.invoke(cli.app, ["trash", "--json"])
    assert [item["name"] for item in json.loads(listed.output)] == ["w"]

    restored = runner.invoke(cli.app, ["untrash", "w"])
    assert restored.exit_code == 0
    assert workspaces.exists("w")


def test_an_empty_trash_says_so_rather_than_printing_nothing(project, on_a_terminal) -> None:
    result = runner.invoke(cli.app, ["trash"])

    assert result.exit_code == 0
    assert "empty" in result.output


def test_untrashing_a_name_that_was_never_deleted_is_a_sentence(project) -> None:
    result = runner.invoke(cli.app, ["untrash", "nope"])

    assert result.exit_code == cli.EXIT_NOT_FOUND
    assert "ccw trash" in result.output
