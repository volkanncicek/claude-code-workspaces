"""Restore planning: which candidates appear, which start checked, and which cannot be opened at all."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_code_workspaces import live, restore, snapshot, workspaces
from claude_code_workspaces.live import LiveSession
from conftest import conversation, live_session, make_repo, write_claude_config, write_transcript


@pytest.fixture
def no_live(monkeypatch: pytest.MonkeyPatch):
    """The crash case: a snapshot exists and nothing is running."""

    def set_live(sessions: list[LiveSession], note: str | None = None):
        monkeypatch.setattr(live, "try_live_sessions", lambda: (sessions, note))

    set_live([])
    return set_live


def age(path: Path, minutes: float) -> None:
    stamp = datetime.now(tz=UTC).timestamp() - minutes * 60
    os.utime(path, (stamp, stamp))


def test_a_snapshot_supplies_the_candidates_and_they_start_checked(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "api")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))
    snapshot.take([live_session("s1", root, name="s1")])

    plan = restore.build_plan()

    assert [entry.session_id for entry in plan.entries] == ["s1"]
    assert plan.entries[0].source == "snapshot"
    assert plan.entries[0].restorable
    assert plan.default_selection() == ["s1"]


def test_the_heuristic_finds_what_no_snapshot_recorded_but_leaves_it_unchecked(home: Path, no_live) -> None:
    """The heuristic is fuzzy at the edges, so it proposes rather than decides."""
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))

    plan = restore.build_plan()

    assert [entry.source for entry in plan.entries] == ["heuristic"]
    assert plan.entries[0].restorable
    assert plan.default_selection() == []
    assert any("No snapshot yet" in note for note in plan.notes)


def test_a_transcript_older_than_the_window_is_not_a_candidate(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    recent = write_transcript(home, "s1", root, conversation("s1", root))
    stale = write_transcript(home, "s2", root, conversation("s2", root))
    age(recent, 1)
    age(stale, 90)

    plan = restore.build_plan(window=timedelta(minutes=15))

    assert [entry.session_id for entry in plan.entries] == ["s1"]


def test_the_window_is_measured_from_the_last_activity_not_from_now(home: Path, no_live) -> None:
    """After a crash the newest transcript is already old; the window has to follow it."""
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    age(write_transcript(home, "s1", root, conversation("s1", root)), 300)
    age(write_transcript(home, "s2", root, conversation("s2", root)), 305)
    age(write_transcript(home, "s3", root, conversation("s3", root)), 400)

    plan = restore.build_plan(window=timedelta(minutes=15))

    assert sorted(entry.session_id for entry in plan.entries) == ["s1", "s2"]


def test_projects_are_alphabetical_and_each_ones_sessions_run_newest_first(home: Path, no_live) -> None:
    """This order is both the checklist's and the pane order, and panes fill two to a tab — so the session you were last in has to come first, not land on the final tab."""
    api, worker = make_repo(home / "code" / "api"), make_repo(home / "code" / "worker")
    write_claude_config(home, {api: True, worker: True})
    age(write_transcript(home, "old", api, conversation("old", api)), 300)
    age(write_transcript(home, "new", api, conversation("new", api)), 10)
    age(write_transcript(home, "mid", worker, conversation("mid", worker)), 100)
    snapshot.take([live_session("old", api, name="old"), live_session("new", api, name="new"), live_session("mid", worker, name="mid")])

    plan = restore.build_plan()

    assert [entry.session_id for entry in plan.entries] == ["new", "old", "mid"]


def test_a_session_with_no_readable_activity_sorts_last_within_its_project(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "api")
    write_claude_config(home, {root: True})
    write_transcript(home, "dated", root, conversation("dated", root))
    snapshot.take([live_session("dated", root, name="dated"), live_session("gone", root, name="gone")])

    plan = restore.build_plan()

    assert [entry.session_id for entry in plan.entries] == ["dated", "gone"]


def test_the_snapshot_wins_over_the_heuristic_for_the_same_session(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))
    snapshot.take([live_session("s1", root, name="s1")])

    plan = restore.build_plan()

    assert [entry.source for entry in plan.entries] == ["snapshot"]


def test_a_running_session_is_listed_but_cannot_be_reopened(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))
    snapshot.take([live_session("s1", root, name="s1")])
    no_live([live_session("s1", root, name="s1")])

    plan = restore.build_plan()

    assert plan.entries[0].live
    assert not plan.entries[0].restorable
    assert plan.default_selection() == []


def test_a_session_whose_transcript_is_gone_is_reported_not_dropped(home: Path, no_live) -> None:
    """Forking assigns a new id and `cleanupPeriodDays` deletes old transcripts."""
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    snapshot.take([live_session("gone", root, name="gone")])

    plan = restore.build_plan()

    assert plan.entries[0].missing
    assert not plan.entries[0].restorable
    assert [entry.session_id for entry in plan.dead] == ["gone"]


def test_a_heuristic_candidate_borrows_its_name_from_the_transcript(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))

    assert restore.build_plan().entries[0].label == "Login bug"


def test_a_candidate_with_no_title_shows_its_id_rather_than_its_first_message(home: Path, no_live) -> None:
    """Measured over the 18 untitled sessions on the reference machine: the first message is a pasted README, a bare filename or a file path, and 14 yield nothing usable. An id is a handle; a misleading sentence is worse than one.

    The session list makes the same choice, so the two agree.
    """
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "abcdef1234", root, conversation("abcdef1234", root, title=None, prompt="why is the login failing"))

    assert restore.build_plan().entries[0].label == "abcdef12"


def test_candidates_are_grouped_by_repository_root_not_by_working_directory(home: Path, no_live) -> None:
    """One tab per project, so a session below the root belongs to the root's tab."""
    root = make_repo(home / "code" / "repo")
    nested = root / "service"
    nested.mkdir()
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))
    write_transcript(home, "s2", nested, conversation("s2", nested))
    snapshot.take([live_session("s1", root, name="s1"), live_session("s2", nested, name="s2")])

    plan = restore.build_plan()
    groups = plan.panes_for(entry.session_id for entry in plan.entries)

    assert list(groups) == [root]
    assert len(groups[root]) == 2


def test_trust_prompts_are_counted_once_per_root(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "repo")
    write_claude_config(home, {})
    write_transcript(home, "s1", root, conversation("s1", root))
    write_transcript(home, "s2", root, conversation("s2", root))
    snapshot.take([live_session("s1", root, name="s1"), live_session("s2", root, name="s2")])
    plan = restore.build_plan()

    assert plan.trust_prompts(["s1", "s2"]) == [root]


def test_a_trusted_root_produces_no_prompt(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "repo")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))
    snapshot.take([live_session("s1", root, name="s1")])
    plan = restore.build_plan()

    assert plan.trust_prompts(["s1"]) == []


def test_the_cap_bounds_the_default_selection(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "repo")
    write_claude_config(home, {root: True})
    for index in range(5):
        write_transcript(home, f"s{index}", root, conversation(f"s{index}", root))
    snapshot.take([live_session(f"s{index}", root, name=f"s{index}") for index in range(5)])

    assert len(restore.build_plan().default_selection(cap=3)) == 3


def test_an_unavailable_live_source_becomes_a_note_not_a_crash(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root))
    no_live([], "`claude` was not found on PATH.")

    plan = restore.build_plan()

    assert any("not found on PATH" in note for note in plan.notes)
    assert plan.entries[0].restorable


def test_an_unreadable_trust_config_is_stated_in_the_plan(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_transcript(home, "s1", root, conversation("s1", root))

    plan = restore.build_plan()

    assert any(".claude.json" in note for note in plan.notes)


def test_nothing_recorded_and_nothing_recent_is_an_empty_plan(home: Path, no_live) -> None:
    write_claude_config(home, {})

    assert restore.build_plan().entries == []


def test_the_checklist_shows_titles_not_the_handles_a_snapshot_stored(home: Path, no_live) -> None:
    """A snapshot records whatever `claude agents` reported, so without a label rule the post-crash checklist is a list of slugs."""
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
    snapshot.take([live_session("s1", root, name=f"{root.name}-3f")])

    assert restore.build_plan().entries[0].label == "Login bug"


def test_a_name_someone_chose_survives_into_the_checklist(home: Path, no_live) -> None:
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
    snapshot.take([live_session("s1", root, name="retry-logic")])

    assert restore.build_plan().entries[0].label == "retry-logic"


class TestStaleSnapshots:
    """A snapshot's age is the time since `ccw` last ran, not since the crash. What this guards against is a set closed on purpose after that run."""

    def prepare(self, home: Path, hours_ago: float) -> None:
        root = make_repo(home / "code" / "app")
        write_claude_config(home, {root: True})
        write_transcript(home, "s1", root, conversation("s1", root))
        snapshot.take([live_session("s1", root, name="s1")])
        path = next(iter(snapshot.snapshots_dir().glob("*.json")))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["takenAt"] = (datetime.now(tz=UTC) - timedelta(hours=hours_ago)).isoformat()
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_a_recent_snapshot_is_ticked(self, home: Path, no_live) -> None:
        self.prepare(home, hours_ago=1)
        plan = restore.build_plan()

        assert not plan.snapshot_is_stale
        assert plan.default_selection() == ["s1"]

    def test_an_old_one_is_offered_rather_than_assumed(self, home: Path, no_live) -> None:
        self.prepare(home, hours_ago=48)
        plan = restore.build_plan()

        assert plan.snapshot_is_stale
        assert plan.default_selection() == []
        assert plan.entries[0].restorable  # listed and openable, just not ticked
        assert any("nothing is ticked" in note for note in plan.notes)

    def test_a_workspace_is_a_decision_and_stays_ticked(self, home: Path, no_live) -> None:
        """However old it is: someone chose that set by name."""
        root = make_repo(home / "code" / "app")
        write_claude_config(home, {root: True})
        write_transcript(home, "s1", root, conversation("s1", root))
        saved = workspaces.from_live("w", [live_session("s1", root, name="s1")])
        saved.updated = datetime.now(tz=UTC) - timedelta(days=90)
        workspaces.save(saved)

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert plan.default_selection() == ["s1"]


class TestNoDuplicates:
    """The only thing stopping a running session being opened a second time is `claude agents --json`."""

    def prepare(self, home: Path) -> Path:
        root = make_repo(home / "code" / "app")
        write_claude_config(home, {root: True})
        write_transcript(home, "s1", root, conversation("s1", root))
        workspaces.save(workspaces.from_live("w", [live_session("s1", root, name="s1")]))
        return root

    def test_a_running_member_is_shown_but_cannot_be_opened(self, home: Path, no_live) -> None:
        root = self.prepare(home)
        no_live([live_session("s1", root, name="s1")])

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert plan.entries[0].live
        assert not plan.entries[0].restorable
        assert plan.default_selection() == []

    def test_the_same_holds_for_the_crash_path(self, home: Path, no_live) -> None:
        root = self.prepare(home)
        snapshot.take([live_session("s1", root, name="s1")])
        no_live([live_session("s1", root, name="s1")])

        plan = restore.build_plan()

        assert not plan.entries[0].restorable
        assert plan.default_selection() == []

    def test_nothing_is_ticked_when_the_live_source_cannot_be_read(self, home: Path, no_live) -> None:
        """Not knowing what is running is not the same as nothing running. Ten running sessions all looked safe to open."""
        self.prepare(home)
        no_live([], "`claude` was not found on PATH.")

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert not plan.liveness_known
        assert plan.default_selection() == []
        assert any("second time" in note for note in plan.notes)

    def test_the_entries_are_still_offered_so_the_choice_stays_yours(self, home: Path, no_live) -> None:
        self.prepare(home)
        no_live([], "`claude` was not found on PATH.")

        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert plan.entries[0].restorable  # listed and openable, just never assumed


class TestNotKnowingWhatRunsBlocksEverySurface:
    """`claude agents --json` is the only guard against opening a second copy of a running session. When it fails there are no live ids, so every entry looks free to open."""

    def test_openable_is_empty_when_liveness_is_unknown(self, home: Path, set_live) -> None:
        root = make_repo(home / "code" / "app")
        for session_id in ("s1", "s2"):
            write_transcript(home, session_id, root, conversation(session_id, root))
        running = [live_session(session_id, root) for session_id in ("s1", "s2")]
        set_live(*running)
        workspaces.save(workspaces.from_live("w", running))

        assert restore.plan_for_workspace(workspaces.load("w")).openable == []

        set_live(unavailable="`claude` was not found on PATH.")
        plan = restore.plan_for_workspace(workspaces.load("w"))

        assert sorted(entry.session_id for entry in plan.entries if entry.restorable) == ["s1", "s2"], "the raw flag is not the test"
        assert plan.openable == [], "and both sessions are in fact running"
        assert any("PATH" in note for note in plan.notes)

    def test_the_command_line_selection_goes_through_the_same_guard(self, home: Path, set_live) -> None:
        root = make_repo(home / "code" / "app")
        write_transcript(home, "s1", root, conversation("s1", root))
        workspaces.save(workspaces.from_live("w", [live_session("s1", root)]))
        set_live(unavailable="`claude` was not found on PATH.")

        assert restore.plan_for_workspace(workspaces.load("w")).default_selection() == []
