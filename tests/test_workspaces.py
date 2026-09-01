"""Named workspaces. The fixed-list decision only holds up if the list stays maintainable, so the affordances get as much attention as the storage."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_code_workspaces import formatting, live, restore, workspaces
from claude_code_workspaces.workspaces import WorkspaceError
from conftest import conversation, live_session, make_repo, write_claude_config, write_transcript


def test_a_workspace_round_trips(home: Path) -> None:
    saved = workspaces.from_live("api-refactor", [live_session("s1", home / "a", name="api-eb"), live_session("s2", home / "b")])
    workspaces.save(saved)

    loaded = workspaces.load("api-refactor")

    assert loaded.name == "api-refactor"
    assert loaded.session_ids == ["s1", "s2"]
    assert loaded.members[0].name == "api-eb"
    assert loaded.members[0].cwd == home / "a"
    assert loaded.members[1].name is None


def test_the_contents_are_frozen_at_save_time(home: Path) -> None:
    """A workspace is a thought, not a rule. New sessions must not appear in it on their own."""
    workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))

    assert workspaces.load("w").session_ids == ["s1"]


@pytest.mark.parametrize("name", ["Api", "with space", "-leading", "", "a" * 65, "sub/dir", ".."])
def test_unusable_names_are_refused(home: Path, name: str) -> None:
    with pytest.raises(WorkspaceError):
        workspaces.validate(name)


@pytest.mark.parametrize("name", ["api", "api-refactor", "ds_2381", "v1.2", "a"])
def test_usable_names_are_accepted(home: Path, name: str) -> None:
    assert workspaces.validate(name) == name


def test_a_store_that_cannot_be_written_is_a_different_failure_from_a_bad_name(home: Path) -> None:
    """One exception type for both made a full disk read as a name the user should change. Still a `WorkspaceError`, so every existing handler keeps working."""
    (home / ".ccw").mkdir(parents=True, exist_ok=True)
    (home / ".ccw" / "workspaces").write_text("a file where the directory has to go", encoding="utf-8")

    with pytest.raises(workspaces.WorkspaceStoreError) as raised:
        workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))

    assert isinstance(raised.value, WorkspaceError)
    assert not isinstance(raised.value, workspaces.WorkspaceRequestError)


def test_a_missing_workspace_says_how_to_find_the_real_ones(home: Path) -> None:
    with pytest.raises(WorkspaceError, match="ccw list"):
        workspaces.load("nope")


def test_a_corrupt_workspace_names_itself(home: Path) -> None:
    workspaces.workspaces_dir().mkdir(parents=True)
    (workspaces.workspaces_dir() / "broken.json").write_text("{ truncated", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="broken"):
        workspaces.load("broken")


def test_a_corrupt_workspace_does_not_take_the_listing_down(home: Path) -> None:
    workspaces.save(workspaces.from_live("good", [live_session("s1", home / "a")]))
    (workspaces.workspaces_dir() / "broken.json").write_text("{ truncated", encoding="utf-8")

    assert [workspace.name for workspace in workspaces.load_all()] == ["good"]


def test_listing_with_no_workspaces_is_empty_not_an_error(home: Path) -> None:
    assert workspaces.load_all() == []


def test_entries_without_a_session_id_are_dropped(home: Path) -> None:
    workspaces.workspaces_dir().mkdir(parents=True)
    (workspaces.workspaces_dir() / "w.json").write_text(json.dumps({"version": workspaces.FILE_VERSION, "name": "w", "members": [{"cwd": "x"}, {"sessionId": "s1", "cwd": "y"}]}), encoding="utf-8")

    assert workspaces.load("w").session_ids == ["s1"]


def test_refresh_replaces_the_contents_and_keeps_the_creation_date(home: Path) -> None:
    original = workspaces.from_live("w", [live_session("s1", home / "a")])
    workspaces.save(original)

    updated = workspaces.refresh("w", [live_session("s2", home / "b"), live_session("s3", home / "c")])

    assert updated.session_ids == ["s2", "s3"]
    assert updated.created == original.created
    assert updated.updated > original.updated or updated.updated >= original.updated


def test_remove_reports_only_what_was_actually_there(home: Path) -> None:
    workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a"), live_session("s2", home / "b")]))

    _, dropped = workspaces.remove("w", ["s2", "never-there"])

    assert dropped == ["s2"]
    assert workspaces.load("w").session_ids == ["s1"]


def test_rename_moves_the_contents_and_leaves_nothing_behind(home: Path) -> None:
    workspaces.save(workspaces.from_live("old", [live_session("s1", home / "a")]))

    workspaces.rename("old", "new")

    assert workspaces.load("new").session_ids == ["s1"]
    assert not workspaces.exists("old")


def test_rename_onto_an_existing_name_is_refused(home: Path) -> None:
    workspaces.save(workspaces.from_live("one", [live_session("s1", home / "a")]))
    workspaces.save(workspaces.from_live("two", [live_session("s2", home / "b")]))

    with pytest.raises(WorkspaceError, match="already exists"):
        workspaces.rename("one", "two")
    assert workspaces.exists("one")


def test_delete_moves_to_the_trash_rather_than_unlinking(home: Path) -> None:
    """Rebuilding a curated set by hand is exactly the cost this tool exists to remove."""
    workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))

    target = workspaces.delete("w")

    assert not workspaces.exists("w")
    assert target.is_file()
    assert target.parent == home / ".ccw" / "trash"
    assert json.loads(target.read_text(encoding="utf-8"))["members"][0]["sessionId"] == "s1"


def test_deleting_something_that_is_not_there_says_so(home: Path) -> None:
    with pytest.raises(WorkspaceError, match="No workspace"):
        workspaces.delete("nope")


def test_no_temporary_files_are_left_behind(home: Path) -> None:
    workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))

    assert list(workspaces.workspaces_dir().glob("*.tmp")) == []


class TestPlanning:
    """A workspace resolved against the world as it is now."""

    def test_members_start_checked_because_they_were_chosen_deliberately(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = make_repo(home / "code" / "app")
        write_claude_config(home, {root: True})
        write_transcript(home, "s1", root, conversation("s1", root))
        monkeypatch.setattr(live, "try_live_sessions", lambda: ([], None))
        workspaces.save(workspaces.from_live("w", [live_session("s1", root)]))

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert [entry.source for entry in plan.entries] == ["workspace"]
        assert plan.default_selection() == ["s1"]

    def test_a_member_that_is_running_again_cannot_be_reopened(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = make_repo(home / "code" / "app")
        write_claude_config(home, {root: True})
        write_transcript(home, "s1", root, conversation("s1", root))
        monkeypatch.setattr(live, "try_live_sessions", lambda: ([live_session("s1", root)], None))
        workspaces.save(workspaces.from_live("w", [live_session("s1", root)]))

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert plan.entries[0].live
        assert not plan.entries[0].restorable

    def test_a_member_whose_transcript_is_gone_is_reported_not_dropped(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Forking assigns a new id and `cleanupPeriodDays` deletes old transcripts, so a fixed list decays."""
        root = make_repo(home / "code" / "app")
        write_claude_config(home, {root: True})
        monkeypatch.setattr(live, "try_live_sessions", lambda: ([], None))
        workspaces.save(workspaces.from_live("w", [live_session("gone", root)]))

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert [entry.session_id for entry in plan.dead] == ["gone"]
        assert plan.default_selection() == []

    def test_an_empty_workspace_says_how_to_fill_it(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(live, "try_live_sessions", lambda: ([], None))
        workspaces.save(workspaces.from_live("w", []))

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert plan.entries == []
        assert any("ccw refresh w" in note for note in plan.notes)

    def test_members_are_grouped_into_one_tab_per_repository(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = make_repo(home / "code" / "repo")
        nested = root / "service"
        nested.mkdir()
        other = make_repo(home / "code" / "other")
        write_claude_config(home, {root: True, other: True})
        for session_id, cwd in (("s1", root), ("s2", nested), ("s3", other)):
            write_transcript(home, session_id, cwd, conversation(session_id, cwd))
        monkeypatch.setattr(live, "try_live_sessions", lambda: ([], None))
        workspaces.save(workspaces.from_live("w", [live_session("s1", root), live_session("s2", nested), live_session("s3", other)]))

        plan = restore.plan_for_workspace(workspaces.load("w"))
        groups = plan.panes_for(entry.session_id for entry in plan.entries)

        assert sorted(path.name for path in groups) == ["other", "repo"]
        assert len(groups[root]) == 2


class TestTheTrashCanBeUndone:
    """Deleting moves rather than unlinks, so recovery has to be reachable from the tool. Otherwise the trash is a slower `rm` that also leaks disk."""

    def test_a_deleted_workspace_is_listed_as_recoverable(self, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a"), live_session("s2", home / "b")]))
        workspaces.delete("w")

        listed = workspaces.trashed()

        assert [(item.name, item.members) for item in listed] == [("w", 2)]

    def test_untrash_brings_it_back_with_its_members(self, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))
        workspaces.delete("w")

        workspaces.untrash("w")

        assert workspaces.exists("w")
        assert workspaces.load("w").session_ids == ["s1"]
        assert workspaces.trashed() == []

    def test_untrash_refuses_to_overwrite_a_live_workspace(self, home: Path) -> None:
        """Silently replacing the current one is the loss the trash exists to prevent."""
        workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))
        workspaces.delete("w")
        workspaces.save(workspaces.from_live("w", [live_session("s2", home / "b")]))

        with pytest.raises(workspaces.WorkspaceError, match="already exists"):
            workspaces.untrash("w")

        assert workspaces.load("w").session_ids == ["s2"]

    def test_untrashing_something_that_was_never_deleted_says_so(self, home: Path) -> None:
        with pytest.raises(workspaces.WorkspaceError, match="in the trash"):
            workspaces.untrash("w")

    def test_the_newest_deletion_wins_when_a_name_was_used_twice(self, home: Path) -> None:
        for session_id in ("old", "new"):
            workspaces.save(workspaces.from_live("w", [live_session(session_id, home / "a")]))
            workspaces.delete("w")
            # The trash stamp has one-second resolution, so age the older file rather than sleeping.
            for path in workspaces.trash_dir().glob("*.json"):
                if path.stat().st_mtime > datetime.now(tz=UTC).timestamp() - 1 and session_id == "old":
                    os.utime(path, (0, 0))

        workspaces.untrash("w")

        assert workspaces.load("w").session_ids == ["new"]

    def test_the_trash_is_bounded_so_it_cannot_leak_disk(self, home: Path) -> None:
        directory = workspaces.trash_dir()
        directory.mkdir(parents=True)
        for index in range(workspaces.KEEP_TRASHED + 5):
            (directory / f"old{index}.json").write_text("{}", encoding="utf-8")
        workspaces.save(workspaces.from_live("w", [live_session("s1", home / "a")]))

        workspaces.delete("w")

        assert len(list(directory.glob("*.json"))) == workspaces.KEEP_TRASHED

    def test_a_file_from_another_version_is_skipped_rather_than_offered(self, home: Path) -> None:
        directory = workspaces.trash_dir()
        directory.mkdir(parents=True)
        (directory / "w.20260101T000000.json").write_text(json.dumps({"name": "w", "sessions": []}), encoding="utf-8")

        assert workspaces.trashed() == []


def test_members_can_be_supplied_without_a_live_session(home: Path) -> None:
    workspaces.save(workspaces.from_members("w", [workspaces.Member("s1", home / "a", "hand-built")]))

    assert workspaces.load("w").session_ids == ["s1"]


def test_adding_prebuilt_members_skips_the_ones_already_there(home: Path) -> None:
    workspaces.save(workspaces.from_members("w", [workspaces.Member("s1", home / "a", None)]))

    _, added = workspaces.add_members("w", [workspaces.Member("s1", home / "a", None), workspaces.Member("s2", home / "b", None)])

    assert added == ["s2"]
    assert workspaces.load("w").session_ids == ["s1", "s2"]


def test_a_timestamp_without_an_offset_does_not_take_down_the_listing(home: Path) -> None:
    """A hand-edited or externally written file can hold `2026-01-01T00:00`. Sorting one naive stamp against the aware ones raises `TypeError`, which kills `ccw list` outright instead of skipping the one file."""
    workspaces.save(workspaces.from_live("aware", [live_session("s1", home / "a")]))
    naive = workspaces.path_for("naive")
    naive.write_text(json.dumps({"version": workspaces.FILE_VERSION, "name": "naive", "created": "2026-01-01T00:00", "updated": "2026-01-01T00:00", "members": []}), encoding="utf-8")

    listed = workspaces.load_all()

    assert workspaces.load("naive").updated.tzinfo is not None
    assert {workspace.name for workspace in listed} == {"aware", "naive"}
    assert formatting.age(workspaces.load("naive").updated).endswith("ago")
