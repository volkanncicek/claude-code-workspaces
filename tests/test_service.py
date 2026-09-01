"""The boundary both front ends sit on.

The point of `service` is that a failure crosses it as an `Outcome` rather than an exception, and that the sentence is written once. These tests are the reason the list can no longer be taken down by a workspace file that moved under it.
"""

import time
from pathlib import Path

import pytest

from claude_code_workspaces import archive, keep, launcher, restore, service, sessions, shutdown, snapshot, workspaces
from conftest import conversation, live_session, make_repo, write_transcript


@pytest.fixture
def project(home: Path):
    return make_repo(home / "code" / "app")


class TestNothingRaisesAcrossTheBoundary:
    """Every one of these used to reach the caller as an exception, which the CLI caught and the list did not."""

    def test_a_missing_workspace_is_an_outcome(self, home: Path) -> None:
        outcome = service.refresh_workspace("nope")

        assert not outcome.ok
        assert "nope" in outcome.message

    def test_a_workspace_that_cannot_be_written_is_an_outcome(self, home: Path, project: Path, set_live, monkeypatch: pytest.MonkeyPatch) -> None:
        set_live(live_session("s1", project))

        def refuse(*_args, **_kwargs):
            raise workspaces.WorkspaceStoreError("Could not write 'x': disk full")

        monkeypatch.setattr(workspaces, "save", refuse)
        outcome = service.save_workspace("x")

        assert not outcome.ok
        assert "disk full" in outcome.message

    def test_deleting_a_workspace_that_is_already_gone_is_an_outcome(self, home: Path) -> None:
        outcome = service.delete_workspace("nope")

        assert not outcome.ok


class TestArchiveTellsTheTruth:
    def test_a_write_that_failed_is_not_reported_as_archived(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reporting a state the file does not hold means the session silently returns at the next reload."""
        monkeypatch.setattr(archive, "_write", lambda _ids: False)

        assert archive.toggle("s1") is None

        outcome = service.archive_session("s1", "Login bug")
        assert not outcome.ok
        assert "Login bug" in outcome.message

    def test_a_write_that_worked_reports_the_direction(self, home: Path) -> None:
        assert service.archive_session("s1", "Login bug").message == "Login bug archived."
        assert service.archive_session("s1", "Login bug").message == "Login bug is back in the list."


class TestNothingRunningIsNotTheSameAsCannotTell:
    """Both refuse, and they must not refuse with the same sentence: one is a fact about the machine, the other is a gap in what the tool knows."""

    def test_an_empty_live_set_says_nothing_is_running(self, home: Path, set_live) -> None:
        outcome = service.save_workspace("x")

        assert not outcome.ok
        assert "Nothing is running" in outcome.message

    def test_an_unreadable_live_source_says_why(self, home: Path, set_live) -> None:
        set_live(unavailable="`claude` was not found on PATH.")
        outcome = service.save_workspace("x")

        assert not outcome.ok
        assert "PATH" in outcome.message


class TestBothSurfacesGetTheSameSentence:
    def test_the_name_is_validated_before_the_live_source_is_read(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unusable name costs no subprocess, and says the same thing whichever surface asked."""

        def forbidden():
            raise AssertionError("the live source must not be read for a name that cannot be used")

        monkeypatch.setattr(service.live_source, "try_live_sessions", forbidden)
        outcome = service.save_workspace("Not A Name")

        assert not outcome.ok
        assert "usable workspace name" in outcome.message

    def test_saving_over_an_existing_workspace_names_the_way_out(self, home: Path, project: Path, set_live) -> None:
        set_live(live_session("s1", project))
        assert service.save_workspace("kept").ok

        outcome = service.save_workspace("kept")

        assert not outcome.ok
        assert "ccw refresh kept" in outcome.message
        assert "--force" in outcome.message

    def test_force_replaces_it(self, home: Path, project: Path, set_live) -> None:
        set_live(live_session("s1", project))
        service.save_workspace("kept")
        set_live(live_session("s2", project))

        assert service.save_workspace("kept", force=True).ok
        assert workspaces.load("kept").session_ids == ["s2"]


class TestBothSurfacesRestoreTheSameWay:
    """Opening panes is a state change, so it goes through `service` like every other one. Before that, the command line snapshotted after a restore and the list did not."""

    def test_the_wording_is_written_once(self, home: Path, project: Path, set_live) -> None:
        set_live(live_session("s1", project))
        service.save_workspace("w")
        plan = restore.plan_for_workspace(workspaces.load("w"))

        outcome = service.launch_restore(plan, ["s1"], source="w")

        assert not outcome.ok, "a running session cannot be reopened"
        assert "already running" in outcome.message

    def test_a_restore_records_what_it_just_rebuilt(self, home: Path, project: Path, set_live, monkeypatch: pytest.MonkeyPatch) -> None:
        """The snapshot after a restore is the only record of the set that was rebuilt."""
        set_live(live_session("s1", project))

        service.record_restored_set(settle=0)

        populated = snapshot.latest_populated()
        assert populated is not None
        assert [entry["sessionId"] for entry in populated[1]] == ["s1"]

    def test_quitting_inside_the_settle_window_does_not_hold_the_process(self, home: Path, project: Path, set_live) -> None:
        """Textual joins thread workers before `App.run` returns, so an uninterruptible settle is quit latency."""
        set_live(live_session("s1", project))
        shutdown.begin()

        started = time.perf_counter()
        service.record_restored_set(settle=30)

        assert time.perf_counter() - started < 1.0
        assert snapshot.latest_populated() is None, "a quit must not leave a snapshot the live read never confirmed"

    def test_nothing_is_recorded_on_the_way_in(self, home: Path, project: Path, set_live) -> None:
        """A crash leaves a reduced live set, so a snapshot taken before the restore would bury the record being recovered from."""
        write_transcript(home, "gone", project, conversation("gone", project))
        set_live()

        restore.build_plan()

        assert snapshot.latest_populated() is None

    def test_a_single_resume_opens_a_tab_in_the_window_you_are_in(self, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write_transcript(home, "s1", project, conversation("s1", project))
        rows = sessions.history_rows()
        opened: list[dict] = []
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": opened.append({"window": window, "fork": fork}))

        outcome = service.resume_session(rows[0], fork=True)

        assert outcome.ok
        assert opened == [{"window": launcher.CURRENT_WINDOW, "fork": True}]
        assert outcome.message.startswith("Forking")

    def test_a_session_that_is_running_is_refused_by_the_service_not_the_caller(self, home: Path, project: Path, set_live) -> None:
        write_transcript(home, "s1", project, conversation("s1", project))
        set_live(live_session("s1", project))
        rows, _ = sessions.collect()

        outcome = service.resume_session(next(row for row in rows if row.session_id == "s1"))

        assert not outcome.ok
        assert "already running" in outcome.message


class TestBothSurfacesResolveANamedRestoreTheSameWay:
    """`plan_named_restore` owns openable / dead / over-cap for the list (named restore is list-only)."""

    @pytest.fixture
    def saved(self, home: Path, project: Path, set_live):
        """A workspace of two sessions, neither of them running."""
        for session_id in ("s1", "s2"):
            write_transcript(home, session_id, project, conversation(session_id, project))
        workspaces.save(workspaces.from_live("w", [live_session(session_id, project) for session_id in ("s1", "s2")]))
        set_live()
        return project

    def test_a_missing_workspace_has_no_plan_to_resolve(self, home: Path) -> None:
        resolved = service.plan_named_restore("nope")

        assert resolved.plan is None
        assert resolved.blocked is not None
        assert "nope" in resolved.blocked.message

    def test_it_opens_everything_openable(self, saved) -> None:
        resolved = service.plan_named_restore("w")

        assert resolved.blocked is None
        assert set(resolved.openable) == {"s1", "s2"}
        assert not resolved.over_cap

    def test_it_asks_only_past_the_cap(self, saved) -> None:
        assert service.plan_named_restore("w", cap=1).over_cap
        assert not service.plan_named_restore("w", cap=2).over_cap

    def test_a_dead_member_is_reported_rather_than_dropped(self, home: Path, saved, set_live) -> None:
        """Dead ids should be surfaced. Dropping one is the user's call, so neither surface does it."""
        transcripts_of = list((home / ".claude" / "projects").rglob("s1.jsonl"))
        for path in transcripts_of:
            path.unlink()

        resolved = service.plan_named_restore("w")

        assert [entry.session_id for entry in resolved.dead] == ["s1"]
        assert resolved.openable == ("s2",), "the rest of the set still opens"
        assert "1 session(s) in 'w'" in resolved.dead_note

    def test_it_refuses_when_it_cannot_tell_what_is_running(self, saved, set_live) -> None:
        """`restorable` reads `entry.live`; with no live ids every member looks free to open."""
        set_live(unavailable="`claude` was not found on PATH.")

        resolved = service.plan_named_restore("w")

        assert resolved.openable == ()
        assert resolved.blocked is not None
        assert "PATH" in resolved.blocked.message, "and the refusal says why rather than guessing at a reason"


class TestADryRunPrintsSomethingThatCanBeRun:
    def test_an_argument_with_a_space_in_it_is_quoted(self, home: Path, monkeypatch: pytest.MonkeyPatch, set_live) -> None:
        """`C:\\Program Files\\...\\wt.exe` and a project under `My Documents` both split at the space otherwise."""
        project = make_repo(home / "code" / "my app")
        write_transcript(home, "s1", project, conversation("s1", project))
        snapshot.take([live_session("s1", project)])
        set_live()

        class Spaced:
            name = "Windows Terminal"

            def build(self, groups, *, fork=False, window=launcher.NEW_WINDOW):
                return [r"C:\Program Files\WindowsApps\wt.exe", "-d", str(project)]

        monkeypatch.setattr(service, "default_launcher", Spaced)
        outcome = service.preview_restore(restore.build_plan(), ["s1"])

        assert outcome.ok
        assert outcome.lines[0] == f'"C:\\Program Files\\WindowsApps\\wt.exe" -d "{project}"'


class TestAFailureCarriesItsClass:
    """The CLI turns `kind` into an exit code, so a caller can branch without matching on the sentence."""

    def test_a_missing_workspace_is_not_the_same_class_as_a_bad_request(self, home: Path, project: Path, set_live) -> None:
        set_live(live_session("s1", project))
        service.save_workspace("kept")

        assert service.refresh_workspace("nope").kind == service.NOT_FOUND
        assert service.save_workspace("Not A Name").kind == service.REFUSED
        assert service.save_workspace("kept").kind == service.REFUSED

    def test_an_unreadable_live_source_is_an_environment_failure(self, home: Path, set_live) -> None:
        set_live(unavailable="`claude` was not found on PATH.")

        assert service.save_workspace("x").kind == service.UNAVAILABLE

    def test_a_launcher_that_cannot_be_driven_is_an_environment_failure(self, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class Refuses:
            name = "Something Else"

            def build(self, groups, *, fork=False, window=launcher.NEW_WINDOW):
                raise launcher.LauncherUnavailable("no server running.")

            launch = build

        monkeypatch.setattr(service, "default_launcher", Refuses)
        write_transcript(home, "s1", project, conversation("s1", project))

        assert service.resume_session(sessions.history_rows()[0]).kind == service.UNAVAILABLE

    def test_an_empty_selection_is_its_own_class(self, home: Path, project: Path, set_live) -> None:
        set_live(live_session("s1", project))
        service.save_workspace("w")

        assert service.launch_restore(restore.plan_for_workspace(workspaces.load("w")), ["s1"]).kind == service.NOTHING


class TestASecondLauncherCanBeDrivenWithoutTouchingThisLayer:
    """The `Launcher` seam is the whole platform story, so it is worth proving rather than asserting in a docstring.

    A tmux launcher is planned. If driving one required understanding Windows Terminal's dialect, the interface would not be doing its job — which is what `CURRENT_WINDOW` being the literal `wt` flag `last` used to mean.
    """

    def stub(self, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        """A launcher with nothing Windows about it, standing in for the tmux one."""
        seen: list[dict] = []

        class Recorder:
            name = "Something Else"

            def build(self, groups, *, fork=False, window=launcher.NEW_WINDOW):
                return ["stub"]

            def launch(self, groups, *, fork=False, window=launcher.NEW_WINDOW):
                seen.append({"roots": sorted(path.name for path in groups), "window": window, "fork": fork})

        monkeypatch.setattr(service, "default_launcher", Recorder)
        return seen

    def test_it_receives_an_intention_not_a_terminal_flag(self, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch, set_live) -> None:
        seen = self.stub(monkeypatch)
        write_transcript(home, "s1", project, conversation("s1", project))
        snapshot.take([live_session("s1", project)])
        set_live()
        plan = restore.build_plan()

        outcome = service.launch_restore(plan, ["s1"], window=launcher.CURRENT_WINDOW)

        assert outcome.ok
        assert seen == [{"roots": ["app"], "window": "current", "fork": False}]
        assert seen[0]["window"] != "last", "`last` is Windows Terminal's spelling; the seam must not carry it"

    def test_a_restored_set_lands_in_the_window_you_are_in(self, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch, set_live) -> None:
        """Asked for on 2026-08-11. A restore used to open a window of its own, which meant leaving the window you were working in to reach it."""
        seen = self.stub(monkeypatch)
        write_transcript(home, "s1", project, conversation("s1", project))
        snapshot.take([live_session("s1", project)])
        set_live()
        plan = restore.build_plan()

        outcome = service.launch_restore(plan, ["s1"])

        assert seen == [{"roots": ["app"], "window": "current", "fork": False}]
        assert "in this window." in outcome.message

    def test_a_single_resume_reaches_it_the_same_way(self, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self.stub(monkeypatch)
        write_transcript(home, "s1", project, conversation("s1", project))

        outcome = service.resume_session(sessions.history_rows()[0], fork=True)

        assert outcome.ok
        assert seen == [{"roots": ["app"], "window": "current", "fork": True}]

    def test_its_own_refusal_is_the_sentence_the_user_sees(self, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The launcher names itself, so a platform that cannot be driven says which one it was."""

        class Refuses:
            name = "Something Else"

            def build(self, groups, *, fork=False, window=launcher.NEW_WINDOW):
                raise launcher.LauncherUnavailable("no server running.")

            launch = build

        monkeypatch.setattr(service, "default_launcher", Refuses)
        write_transcript(home, "s1", project, conversation("s1", project))

        outcome = service.resume_session(sessions.history_rows()[0])

        assert not outcome.ok
        assert outcome.message == "Something Else cannot be driven here: no server running."


class TestKeepingAndArchivingAreOpposites:
    def test_keeping_a_session_reports_the_direction_it_went(self, home: Path) -> None:
        kept = service.keep_session("s1", "Login bug")
        released = service.keep_session("s1", "Login bug")

        assert kept.ok and "Login bug" in kept.message
        assert released.ok and kept.message != released.message

    def test_keeping_an_archived_session_brings_it_back(self, home: Path) -> None:
        """Hidden and kept is not a state anyone can mean."""
        service.archive_session("s1", "Login bug")

        service.keep_session("s1", "Login bug")

        assert keep.kept() == {"s1"}
        assert archive.archived() == set()

    def test_archiving_a_kept_session_releases_it(self, home: Path) -> None:
        service.keep_session("s1", "Login bug")

        service.archive_session("s1", "Login bug")

        assert archive.archived() == {"s1"}
        assert keep.kept() == set()

    def test_releasing_a_session_leaves_the_other_store_alone(self, home: Path) -> None:
        """Exclusion applies to setting a mark, not to clearing one."""
        service.archive_session("s1", "Login bug")
        service.archive_session("s1", "Login bug")

        assert archive.archived() == set()
        assert keep.kept() == set()

    def test_an_unwritable_store_fails_loudly(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service.keep, "toggle", lambda _: None)

        outcome = service.keep_session("s1", "Login bug")

        assert not outcome.ok
        assert "~/.ccw" in outcome.message


class TestAddingSessionsThatAreNotRunning:
    def row(self, session_id: str, cwd: Path) -> sessions.SessionRow:
        return sessions.SessionRow(session_id=session_id, cwd=cwd, root=cwd, title="Login bug", agent_name=None, archived=False, status=None, last_active=None, transcript=cwd / f"{session_id}.jsonl", branch=None, first_prompt=None)

    def test_a_finished_session_can_be_added(self, home: Path) -> None:
        """The defect: a workspace member is an id and a cwd, both of which a transcript carries."""
        workspaces.save(workspaces.from_members("w", []))

        outcome = service.add_sessions("w", ["dead1"], [self.row("dead1", home / "a")])

        assert outcome.ok
        assert workspaces.load("w").session_ids == ["dead1"]

    def test_an_id_nobody_has_ever_seen_is_still_refused(self, home: Path) -> None:
        workspaces.save(workspaces.from_members("w", []))

        outcome = service.add_sessions("w", ["typo"], [self.row("dead1", home / "a")])

        assert not outcome.ok
        assert "typo" in outcome.message

    def test_the_recorded_member_keeps_the_cwd_the_transcript_gave_it(self, home: Path) -> None:
        workspaces.save(workspaces.from_members("w", []))

        service.add_sessions("w", ["dead1"], [self.row("dead1", home / "a")])

        assert workspaces.load("w").members[0].cwd == home / "a"
