"""The tool reads Claude Code's data and never changes it.

This is the promise that makes `ccw` safe to point at real work, and it is checked rather than asserted in a docstring. An earlier version moved transcripts out of `~/.claude/projects/` and that is exactly the kind of thing this would have caught: everything the tool writes belongs under `~/.ccw/`.
"""

from pathlib import Path

import pytest

from claude_code_workspaces import archive, restore, service, sessions, transcripts, workspaces
from conftest import conversation, live_session, make_repo, write_claude_config, write_transcript


def fingerprint(home: Path) -> dict[str, tuple[int, bytes]]:
    """Every file Claude Code owns, with its size and contents."""
    owned = [*(home / ".claude").rglob("*"), home / ".claude.json"]
    return {str(path.relative_to(home)): (path.stat().st_size, path.read_bytes()) for path in owned if path.is_file()}


@pytest.fixture
def claude_data(home: Path, set_live):
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    for session_id in ("s1", "s2"):
        write_transcript(home, session_id, root, conversation(session_id, root))
    write_transcript(home, "ghost", root, [])  # the kind of file `ccw clean` used to move
    set_live(live_session("s1", root))
    return root


def test_reading_the_session_list_changes_nothing(claude_data, home: Path) -> None:
    before = fingerprint(home)

    sessions.collect()
    sessions.history_rows()

    assert fingerprint(home) == before


def test_planning_a_restore_changes_nothing(claude_data, home: Path) -> None:
    before = fingerprint(home)

    restore.build_plan()

    assert fingerprint(home) == before


def test_the_whole_workspace_lifecycle_changes_nothing(claude_data, home: Path) -> None:
    before = fingerprint(home)

    service.save_workspace("w")
    service.add_sessions("w", ["s1"], sessions.collect()[0])
    service.remove_sessions("w", ["s1"])
    service.refresh_workspace("w")
    restore.plan_for_workspace(workspaces.load("w"))
    service.delete_workspace("w")

    assert fingerprint(home) == before


def test_archiving_changes_nothing(claude_data, home: Path) -> None:
    """Archiving is the answer to a cluttered list precisely because it costs nothing on disk."""
    before = fingerprint(home)

    service.archive_session("s1", "Login bug")
    sessions.history_rows()
    service.archive_session("s1", "Login bug")

    assert fingerprint(home) == before
    assert archive.archived() == set()


def test_nothing_the_tool_writes_lands_outside_its_own_directory(claude_data, home: Path) -> None:
    before = {path for path in home.rglob("*") if path.is_file()}

    service.save_workspace("w")
    sessions.collect()
    restore.build_plan()

    appeared = {path for path in home.rglob("*") if path.is_file()} - before
    outside = {path for path in appeared if not path.is_relative_to(home / ".ccw")}

    assert appeared, "the fixture must actually exercise something that writes"
    assert outside == set(), f"written outside ~/.ccw: {sorted(str(path.relative_to(home)) for path in outside)}"


def test_there_is_no_way_to_move_a_transcript(claude_data) -> None:
    """`ccw clean` and the list's `d` key are gone. If either comes back, it comes back with a decision behind it."""
    assert not hasattr(transcripts, "trash_transcript")
    assert not any(name.endswith("clean") for name in dir(service))
