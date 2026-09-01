"""Snapshots. The history exists so that a crash cannot erase the record of what the crash destroyed."""

import json
from datetime import UTC, datetime
from pathlib import Path

from claude_code_workspaces import snapshot
from claude_code_workspaces.live import LiveSession


def session(session_id: str, cwd: Path, *, name: str | None = None) -> LiveSession:
    return LiveSession(session_id=session_id, cwd=cwd, status="idle", name=name, started_at=datetime.now(tz=UTC))


def test_a_snapshot_round_trips(home: Path) -> None:
    snapshot.take([session("s1", home / "a", name="api-eb"), session("s2", home / "b")])

    populated = snapshot.latest_populated()

    assert populated is not None
    taken_at, entries = populated

    assert taken_at.tzinfo is not None
    assert [entry["sessionId"] for entry in entries] == ["s1", "s2"]
    assert entries[0]["name"] == "api-eb"


def test_no_snapshots_at_all_reads_as_none(home: Path) -> None:
    assert snapshot.latest_populated() is None


def test_an_empty_snapshot_never_shadows_a_populated_one(home: Path) -> None:
    """The crash case: the first `ccw` after a crash must not be able to bury the useful record."""
    snapshot.take([session("s1", home / "a")])
    snapshot.take([])

    populated = snapshot.latest_populated()

    assert populated is not None
    _, entries = populated

    assert [entry["sessionId"] for entry in entries] == ["s1"]


def test_a_corrupt_snapshot_is_skipped_for_the_next_one_down(home: Path) -> None:
    snapshot.take([session("s1", home / "a")])
    corrupt = snapshot.snapshots_dir() / "20991231T235959Z.json"
    corrupt.write_text("{ truncated", encoding="utf-8")

    populated = snapshot.latest_populated()

    assert populated is not None
    _, entries = populated

    assert [entry["sessionId"] for entry in entries] == ["s1"]


def test_entries_without_a_session_id_are_dropped(home: Path) -> None:
    path = snapshot.snapshots_dir()
    path.mkdir(parents=True)
    (path / "20260101T000000Z.json").write_text(json.dumps({"takenAt": "2026-01-01T00:00:00+00:00", "sessions": [{"cwd": "x"}, {"sessionId": "s1", "cwd": "y"}]}), encoding="utf-8")

    populated = snapshot.latest_populated()

    assert populated is not None
    _, entries = populated

    assert [entry["sessionId"] for entry in entries] == ["s1"]


def test_the_history_is_pruned_to_the_keep_limit(home: Path) -> None:
    directory = snapshot.snapshots_dir()
    directory.mkdir(parents=True)
    for index in range(snapshot.KEEP + 5):
        (directory / f"20260101T{index:04d}00Z.json").write_text('{"sessions": []}', encoding="utf-8")

    snapshot.take([session("fresh", home / "a")])
    remaining = sorted(path.name for path in directory.glob("*.json"))

    assert len(remaining) == snapshot.KEEP
    assert remaining[0] == "20260101T000600Z.json"  # the five oldest were dropped, newest kept


def test_no_temporary_files_are_left_behind(home: Path) -> None:
    snapshot.take([session("s1", home / "a")])

    assert list(snapshot.snapshots_dir().glob("*.tmp")) == []
