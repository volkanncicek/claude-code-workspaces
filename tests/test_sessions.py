"""The merged session list, and the transitions the TUI reacts to."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_code_workspaces import archive, formatting, keep, live, repos, sessions, transcripts
from conftest import conversation, live_session, make_repo, write_transcript


@pytest.fixture
def sources(home: Path, set_live):
    return make_repo(home / "code" / "app"), set_live


def test_a_running_session_carries_its_transcripts_detail(sources) -> None:
    root, set_live = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root, title="Login bug", branch="feature/x"))
    set_live(live_session("s1", root, name="app-eb", status="busy"))

    rows, note = sessions.collect()

    assert note is None
    assert len(rows) == 1
    assert rows[0].live
    assert rows[0].status == "busy"
    assert rows[0].label == "Login bug"  # the generated handle does not describe anything
    assert rows[0].branch == "feature/x"
    assert rows[0].first_prompt == "why is the login failing"


def test_a_running_session_falls_back_to_the_transcript_title(sources) -> None:
    root, set_live = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root, title="Login bug"))
    set_live(live_session("s1", root, name=None))

    assert sessions.collect()[0][0].label == "Login bug"


def test_a_session_appears_once_even_though_both_sources_hold_it(sources) -> None:
    root, set_live = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root))
    set_live(live_session("s1", root))

    assert [row.session_id for row in sessions.collect()[0]] == ["s1"]


def test_historical_sessions_are_listed_without_a_status(sources) -> None:
    root, _ = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root))

    rows, _ = sessions.collect()

    assert not rows[0].live
    assert rows[0].status is None
    assert rows[0].glyph == sessions.HISTORICAL_GLYPH


def test_running_sessions_sort_above_everything_else(sources) -> None:
    root, set_live = sources
    for session_id in ("old", "new", "running"):
        write_transcript(root.parent.parent, session_id, root, conversation(session_id, root))
    set_live(live_session("running", root))

    rows, _ = sessions.collect()

    assert rows[0].session_id == "running"


def test_an_unavailable_live_source_still_lists_the_history(sources) -> None:
    root, set_live = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root))

    set_live(unavailable="`claude` was not found on PATH.")
    rows, note = sessions.collect()

    assert note is not None and "PATH" in note
    assert [row.session_id for row in rows] == ["s1"]


class TestSearch:
    def row(self, *, session_id: str = "abc123", name: str | None = "Login bug", branch: str | None = "main", first_prompt: str | None = "why is the login failing") -> sessions.SessionRow:
        project = Path("C:/code/api")
        return sessions.SessionRow(session_id=session_id, cwd=project, root=project, title=name, agent_name=None, archived=False, status=None, last_active=None, transcript=None, branch=branch, first_prompt=first_prompt)

    def test_an_empty_query_keeps_everything(self) -> None:
        rows = [self.row(), self.row(session_id="def")]

        assert sessions.search(rows, "   ") == rows

    @pytest.mark.parametrize("query", ["login bug", "LOGIN", "api", "main", "login failing", "abc123"])
    def test_every_visible_field_is_searchable(self, query: str) -> None:
        assert sessions.search([self.row()], query)

    def test_pasting_a_session_id_narrows_to_that_one_row(self) -> None:
        rows = [self.row(session_id="abc123"), self.row(session_id="zzz999", name="Other", first_prompt="x", branch=None)]

        assert [row.session_id for row in sessions.search(rows, "zzz999")] == ["zzz999"]

    def test_a_query_that_matches_nothing_returns_nothing(self) -> None:
        assert sessions.search([self.row()], "kubernetes") == []


class TestWaiting:
    def row(self, session_id: str, status: str | None) -> sessions.SessionRow:
        return sessions.SessionRow(session_id=session_id, cwd=Path("C:/x"), root=Path("C:/x"), title=None, agent_name=None, archived=False, status=status, last_active=None, transcript=None, branch=None, first_prompt=None)

    def test_only_the_transition_into_waiting_is_reported(self) -> None:
        """A session that has been waiting for ten minutes is not news."""
        rows = [self.row("s1", "waiting")]

        assert [row.session_id for row in sessions.waiting_now({}, rows)] == ["s1"]
        assert sessions.waiting_now({"s1": "waiting"}, rows) == []

    def test_going_from_busy_to_waiting_is_news(self) -> None:
        rows = [self.row("s1", "waiting")]

        assert len(sessions.waiting_now({"s1": "busy"}, rows)) == 1

    def test_historical_rows_never_report(self) -> None:
        assert sessions.waiting_now({}, [self.row("s1", None)]) == []

    def test_statuses_ignores_rows_that_are_not_running(self) -> None:
        assert sessions.statuses([self.row("s1", "idle"), self.row("s2", None)]) == {"s1": "idle"}


class _FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz=None) -> datetime:
        return self._moment


def test_transcripts_module_reports_no_errors_for_well_formed_files(home: Path) -> None:
    root = home / "code" / "app"
    write_transcript(home, "s1", root, conversation("s1", root))
    transcripts.historical_sessions()

    assert transcripts.errors() == []


class TestPolling:
    """Reading the live source and folding it in are separate, so the read can happen off a UI thread."""

    def rows_for(self, root: Path, home: Path) -> list[sessions.SessionRow]:
        for session_id in ("s1", "s2"):
            write_transcript(home, session_id, root, conversation(session_id, root))
        return sessions.collect()[0]

    def test_a_status_change_is_picked_up(self, sources, home: Path) -> None:
        root, set_live = sources
        set_live(live_session("s1", root, status="idle"))
        rows = self.rows_for(root, home)

        set_live(live_session("s1", root, status="waiting"))
        fresh, note = live.try_live_sessions()
        refreshed = sessions.merge_live(rows, fresh)

        assert note is None
        assert next(row for row in refreshed if row.session_id == "s1").status == "waiting"

    def test_a_session_that_ended_stops_being_live(self, sources, home: Path) -> None:
        root, set_live = sources
        set_live(live_session("s1", root))
        rows = self.rows_for(root, home)

        set_live()
        refreshed = sessions.merge_live(rows, live.try_live_sessions()[0])

        assert all(not row.live for row in refreshed)

    def test_a_session_this_list_has_never_seen_joins_it_in_place(self, sources, home: Path) -> None:
        """A whole new row is built from its own transcript rather than costing a rebuild of all 290."""
        root, set_live = sources
        rows = self.rows_for(root, home)
        write_transcript(home, "brand-new", root, conversation("brand-new", root, title="Just started"))

        set_live(live_session("brand-new", root))
        refreshed = sessions.merge_live(rows, live.try_live_sessions()[0])

        appeared = next(row for row in refreshed if row.session_id == "brand-new")
        assert appeared.live
        assert appeared.label == "Just started"

    def test_a_new_session_with_no_transcript_yet_still_appears(self, sources, home: Path) -> None:
        """It has only just started, so there may be nothing on disk. It must not be dropped for that."""
        root, set_live = sources
        rows = self.rows_for(root, home)

        set_live(live_session("no-transcript", root, name="chosen"))
        refreshed = sessions.merge_live(rows, live.try_live_sessions()[0])

        appeared = next(row for row in refreshed if row.session_id == "no-transcript")
        assert appeared.live
        assert appeared.label == "chosen"

    def test_an_unavailable_source_is_not_the_same_as_nothing_running(self, sources, home: Path, set_live) -> None:
        """A failed read marks everything historical, but must not claim a session vanished for good."""
        root, _ = sources
        set_live(live_session("s1", root))
        rows = self.rows_for(root, home)

        set_live(unavailable="`claude` was not found on PATH.")
        fresh, note = live.try_live_sessions()
        refreshed = sessions.merge_live(rows, fresh)

        assert fresh is None
        assert note is not None
        assert all(not row.live for row in refreshed)

    def test_running_sessions_stay_at_the_top_after_a_poll(self, sources, home: Path) -> None:
        root, set_live = sources
        rows = self.rows_for(root, home)

        set_live(live_session("s2", root))
        refreshed = sessions.merge_live(rows, live.try_live_sessions()[0])

        assert refreshed[0].session_id == "s2"

    def test_folding_a_poll_in_touches_no_files(self, sources, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The common poll finds nothing new and runs on the message loop, so it must not do the work that made polling block it."""
        root, set_live = sources
        rows = self.rows_for(root, home)
        set_live(live_session("s1", root))
        fresh, _ = live.try_live_sessions()

        def forbidden(*_args, **_kwargs):
            raise AssertionError("merge_live must not read transcripts, resolve git roots, or spawn claude")

        monkeypatch.setattr(sessions.transcripts, "historical_sessions", forbidden)
        monkeypatch.setattr(sessions, "git_roots", forbidden)
        monkeypatch.setattr(sessions.archive, "archived", forbidden)
        monkeypatch.setattr(live, "try_live_sessions", forbidden)

        sessions.merge_live(rows, fresh)


def test_git_roots_resolves_a_batch_to_the_same_answers_as_one_at_a_time(home: Path) -> None:

    repo = make_repo(home / "code" / "repo")
    nested = repo / "service"
    nested.mkdir()
    plain = home / "notes"
    plain.mkdir()

    repos.forget_roots()
    batched = repos.git_roots([repo, nested, plain, repo])
    repos.forget_roots()
    one_by_one = {path: repos.git_root(path) for path in (repo, nested, plain)}

    assert batched == one_by_one
    assert batched[nested] == repo


def test_git_roots_handles_an_empty_batch(home: Path) -> None:

    assert repos.git_roots([]) == {}


class TestLabels:
    """The list exists to be read. Nothing here invents a name; it only chooses between ones that exist."""

    def test_a_generated_handle_loses_to_the_transcript_title(self, sources, home: Path) -> None:
        root, set_live = sources
        write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
        set_live(live_session("s1", root, name=f"{root.name}-3f"))

        row = sessions.collect()[0][0]

        assert row.label == "Login bug"
        assert row.agent_name == f"{root.name}-3f"

    def test_a_name_someone_chose_beats_the_title(self, sources, home: Path) -> None:
        """`claude --name` exists, so a name that is not the generated shape was set deliberately."""
        root, set_live = sources
        write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
        set_live(live_session("s1", root, name="retry-logic"))

        row = sessions.collect()[0][0]

        assert row.label == "retry-logic"

    def test_the_handle_is_still_searchable(self, sources, home: Path) -> None:
        root, set_live = sources
        write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
        set_live(live_session("s1", root, name=f"{root.name}-3f"))
        rows = sessions.collect()[0]

        assert sessions.search(rows, f"{root.name}-3f")

    def test_a_session_with_neither_falls_back_to_its_id(self, sources, home: Path) -> None:
        root, _ = sources
        write_transcript(home, "abcdef1234", root, conversation("abcdef1234", root, title=None, prompt=""))

        assert sessions.collect()[0][0].label == "abcdef12"

    @pytest.mark.parametrize("generated", ["app-33", "app-2a", "app-ff"])
    def test_the_generated_shape_is_recognised(self, home: Path, generated: str) -> None:
        assert formatting.is_generated_name(generated, home / "code" / "app")

    @pytest.mark.parametrize("chosen", ["app", "app-zz", "app-333", "retry-logic", "App-33"])
    def test_anything_else_is_treated_as_chosen(self, home: Path, chosen: str) -> None:
        assert not formatting.is_generated_name(chosen, home / "code" / "app")


class TestBranch:
    """`HEAD` is Claude Code's "could not tell", not a branch."""

    def test_head_is_not_shown_as_a_branch(self, sources, home: Path) -> None:
        root, _ = sources
        write_transcript(home, "s1", root, conversation("s1", root, branch="HEAD"))

        assert sessions.collect()[0][0].branch is None

    def test_a_real_branch_is_kept(self, sources, home: Path) -> None:
        root, _ = sources
        write_transcript(home, "s1", root, conversation("s1", root, branch="PROJ-2380"))

        assert sessions.collect()[0][0].branch == "PROJ-2380"

    def test_a_real_branch_beats_an_earlier_head_in_the_same_session(self, sources, home: Path) -> None:
        """Five transcripts record both. Taking the first value seen would show the placeholder."""
        root, _ = sources
        records = conversation("s1", root, title=None)
        records[-2]["gitBranch"] = "HEAD"
        records[-1]["gitBranch"] = "PROJ-2380"
        write_transcript(home, "s1", root, records)

        assert sessions.collect()[0][0].branch == "PROJ-2380"


class TestArchive:
    """Archiving records an id and moves nothing, which is what lets it be one key with no confirmation."""

    def test_archiving_hides_nothing_on_disk(self, sources, home: Path) -> None:
        root, _ = sources
        path = write_transcript(home, "s1", root, conversation("s1", root))

        archive.toggle("s1")

        assert path.exists()
        assert sessions.collect()[0][0].archived

    def test_toggling_again_brings_it_back(self, home: Path) -> None:
        assert archive.toggle("s1") is True
        assert archive.toggle("s1") is False
        assert archive.archived() == set()

    def test_nothing_archived_by_default(self, home: Path) -> None:
        assert archive.archived() == set()

    def test_a_broken_file_hides_no_sessions(self, home: Path) -> None:
        """Failing open matters more here than anywhere: failing closed would make sessions vanish."""
        archive.archive_file().parent.mkdir(parents=True, exist_ok=True)
        archive.archive_file().write_text("{ truncated", encoding="utf-8")

        assert archive.archived() == set()

    def test_a_transcript_scan_that_failed_erases_nothing(self, sources, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """One antivirus lock on `~/.claude/projects` used to prune every archived id at once, because a scan that could not look read the same as a scan that found nothing."""
        root, _ = sources
        write_transcript(home, "s1", root, conversation("s1", root))
        archive.toggle("s1")
        archive.toggle("s2")

        class Unreadable:
            name = "projects"

            def glob(self, _pattern: str):
                raise PermissionError("access is denied")

        monkeypatch.setattr(transcripts, "claude_projects", Unreadable)
        sessions.collect()

        assert archive.archived() == {"s1", "s2"}

    def test_ids_whose_sessions_are_gone_are_forgotten(self, sources, home: Path) -> None:
        root, _ = sources
        write_transcript(home, "s1", root, conversation("s1", root))
        archive.toggle("s1")
        archive.toggle("vanished")

        sessions.collect()

        assert archive.archived() == {"s1"}


def test_collect_is_exactly_what_the_progressive_path_composes(sources) -> None:
    """`collect` calls itself the definition the TUI is checked against, and the TUI does not call it: it paints `history_rows` and folds `merge_live` in when the live read lands.

    Without this the equivalence is a docstring, and the two paths could drift a long way before anything failed.
    """
    root, set_live = sources
    home = root.parent.parent
    write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
    write_transcript(home, "s2", root, conversation("s2", root, title="Rate limits"))
    set_live(live_session("s1", root, status="busy"))

    at_once, note = sessions.collect()

    running, progressive_note = live.try_live_sessions()
    progressive = sessions.merge_live(sessions.history_rows(), running)
    sessions.prune_archive(progressive)

    def shape(rows: list[sessions.SessionRow]) -> list[tuple]:
        return [(row.session_id, row.label, row.live, row.status, row.archived) for row in rows]

    assert shape(progressive) == shape(at_once)
    assert progressive_note == note


def test_rows_carry_the_keep_mark_recorded_for_them(sources) -> None:
    root, _ = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root))
    write_transcript(root.parent.parent, "s2", root, conversation("s2", root))
    keep.toggle("s1")

    rows, _ = sessions.collect()

    assert {row.session_id: row.kept for row in rows} == {"s1": True, "s2": False}


def test_a_live_only_session_carries_its_mark_too(sources) -> None:
    """`_from_live` builds rows the transcript scan never saw, and reads the store separately."""
    root, _ = sources
    keep.toggle("live1")

    rows = sessions.merge_live([], [live_session("live1", root)])

    assert rows[0].kept is True


def test_collecting_never_prunes_a_mark_whose_transcript_has_gone(sources) -> None:
    """`prune_archive` drops archived ids that no longer exist; keep must not join that path, because the user asked not to lose these."""
    root, _ = sources
    write_transcript(root.parent.parent, "s1", root, conversation("s1", root))
    keep.toggle("vanished")

    sessions.collect()

    assert keep.kept() == {"vanished"}


class TestTheShelfSortsToTheTop:
    def row(self, session_id: str, *, live: bool = False, kept: bool = False, age: int = 0) -> sessions.SessionRow:
        project = Path("C:/code/api")
        return sessions.SessionRow(session_id=session_id, cwd=project, root=project, title=None, agent_name=None, archived=False, status="idle" if live else None, last_active=datetime.fromtimestamp(age, tz=UTC), transcript=None, branch=None, first_prompt=None, kept=kept)

    def test_kept_sessions_sit_above_the_rest(self) -> None:
        rows = [self.row("plain", age=200), self.row("kept", kept=True, age=100)]

        assert [row.session_id for row in sessions.ordered(rows)] == ["kept", "plain"]

    def test_live_sessions_still_come_first(self) -> None:
        """A running session needs you now; the shelf is for later."""
        rows = [self.row("kept", kept=True, age=200), self.row("live", live=True, age=100)]

        assert [row.session_id for row in sessions.ordered(rows)] == ["live", "kept"]

    def test_recency_still_orders_within_each_band(self) -> None:
        rows = [self.row("old", kept=True, age=100), self.row("new", kept=True, age=200)]

        assert [row.session_id for row in sessions.ordered(rows)] == ["new", "old"]
