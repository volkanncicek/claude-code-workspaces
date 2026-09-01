"""The modal dialogs, driven on their own rather than through the session list.

A dialog's contract is what it dismisses with, so every test here reads the value the caller would receive: `None` and `False` are answers, not absences. The session list is deliberately not the host — its bindings, workers and first paint are noise for a question about one screen.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Input, Label, SelectionList

from claude_code_workspaces import workspaces
from claude_code_workspaces.dialogs import MAX_TABLE_ROWS, AddSessions, AskName, Confirm, Dialog, NameWorkspace, RenameWorkspace, WorkspaceMembers, WorkspacePicker
from claude_code_workspaces.restore import RestoreEntry
from claude_code_workspaces.sessions import SessionRow

ROOT = Path("C:/code/app")


class Host(App[None]):
    """Nothing but a place to push one dialog into."""


@asynccontextmanager
async def opened(screen: Dialog, *, size: tuple[int, int] = (100, 40)):
    """Push one dialog and hand back the pilot plus the list its dismissal lands in.

    `answers` stays empty for as long as the dialog is up, so "did it refuse to close" is a length check rather than an inspection of the screen stack.
    """
    app = Host()
    async with app.run_test(size=size, notifications=True) as pilot:
        answers: list = []
        app.push_screen(screen, callback=answers.append)
        await pilot.pause()
        await pilot.pause()
        yield app, pilot, answers


async def settle(pilot) -> None:
    """A dismissal unwinds over a couple of frames; the callback runs on the last of them."""
    for _ in range(4):
        await pilot.pause()
        await asyncio.sleep(0.01)


def workspace(name: str, *, members: int = 0) -> workspaces.Workspace:
    now = datetime.now(tz=UTC)
    return workspaces.Workspace(name=name, members=[workspaces.Member(f"{name}-{index}", ROOT, None) for index in range(members)], created=now, updated=now)


def entry(session_id: str, *, transcript: bool = True, live: bool = False, title: str | None = "Login bug") -> RestoreEntry:
    return RestoreEntry(session_id=session_id, cwd=ROOT, root=ROOT, agent_name=None, title=title, source="workspace", last_active=datetime.now(tz=UTC), transcript=ROOT / f"{session_id}.jsonl" if transcript else None, live=live)


def row(session_id: str, *, title: str | None = "Login bug") -> SessionRow:
    return SessionRow(session_id=session_id, cwd=ROOT, root=ROOT, title=title, agent_name=None, archived=False, status=None, last_active=datetime.now(tz=UTC), transcript=None, branch=None, first_prompt=None)


def error_of(screen) -> str:
    return str(screen.query_one("#error", Label).render())


def cells(screen) -> list[list[str]]:
    table = screen.query_one(DataTable)
    return [[str(cell) for cell in table.get_row_at(index)] for index in range(table.row_count)]


class TestAskNameRefusesWithoutClosing:
    """A modal cannot report a bad name by returning one, so the refusal has to be visible and the box has to stay up."""

    async def test_a_name_the_pattern_rejects_is_named_in_the_box(self, home: Path) -> None:
        screen = NameWorkspace(2)
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(Input).value = "Bad Name"
            await pilot.press("enter")
            await settle(pilot)

            assert answers == [], "a refused name must not close the box"
            assert "not a usable workspace name" in error_of(screen)

    async def test_a_name_already_on_disk_is_refused_with_the_way_out(self, home: Path) -> None:
        workspaces.save(workspaces.from_members("taken", []))
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(Input).value = "taken"
            await pilot.press("enter")
            await settle(pilot)

            assert answers == []
            assert "already exists" in error_of(screen)
            assert "refresh it" in error_of(screen), "the message has to say what to do instead"

    async def test_an_empty_box_is_refused_rather_than_dismissed(self, home: Path) -> None:
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("enter")
            await settle(pilot)

            assert answers == []
            assert error_of(screen) != ""

    async def test_surrounding_space_is_dropped_rather_than_making_the_name_unusable(self, home: Path) -> None:
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(Input).value = "  api-refactor  "
            await pilot.press("enter")
            await settle(pilot)

            assert answers == ["api-refactor"]

    async def test_a_corrected_name_goes_through_after_a_refusal(self, home: Path) -> None:
        """The first attempt leaves an error on screen; that must not latch the box shut."""
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(Input).value = "Bad Name"
            await pilot.press("enter")
            await settle(pilot)
            assert answers == []

            screen.query_one(Input).value = "good-name"
            await pilot.press("enter")
            await settle(pilot)

            assert answers == ["good-name"]

    async def test_a_second_box_opens_without_the_first_one_s_complaint(self, home: Path) -> None:
        """Every open is a fresh screen, so a refusal cannot follow the user into the next attempt."""
        first = NameWorkspace(1)
        async with opened(first) as (app, pilot, _answers):
            first.query_one(Input).value = "Bad Name"
            await pilot.press("enter")
            await settle(pilot)
            assert error_of(first) != ""

            second = NameWorkspace(1)
            app.push_screen(second)
            await settle(pilot)

            assert error_of(second) == ""


class TestAskNameAnswers:
    async def test_escape_answers_none(self, home: Path) -> None:
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("escape")
            await settle(pilot)

            assert answers == [None]

    async def test_the_cancel_button_answers_none(self, home: Path) -> None:
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one("#cancel", Button).press()
            await settle(pilot)

            assert answers == [None]

    async def test_the_confirm_button_answers_the_typed_name(self, home: Path) -> None:
        screen = NameWorkspace(1)
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(Input).value = "api"
            screen.query_one("#confirm", Button).press()
            await settle(pilot)

            assert answers == ["api"]

    async def test_the_caret_starts_in_the_name_box(self, home: Path) -> None:
        """Anywhere else and the first keystroke is lost or, worse, taken as a binding."""
        screen = NameWorkspace(1)
        async with opened(screen) as (app, _pilot, _answers):
            assert app.focused is not None and app.focused.id == "name"

    async def test_a_suggestion_is_offered_already_typed(self, home: Path) -> None:
        screen = NameWorkspace(3, "api")
        async with opened(screen) as (_app, pilot, answers):
            assert screen.query_one(Input).value == "api"
            await pilot.press("enter")
            await settle(pilot)

            assert answers == ["api"], "accepting the suggestion is one keystroke"

    async def test_the_two_boxes_say_which_job_they_are_doing(self) -> None:
        """Both are `AskName`; the only thing separating them for a reader is the title and the button."""
        assert (NameWorkspace.box_title, NameWorkspace.confirm_label) == ("Save workspace", "Save")
        assert (RenameWorkspace.box_title, RenameWorkspace.confirm_label) == ("Rename workspace", "Rename")
        assert issubclass(NameWorkspace, AskName) and issubclass(RenameWorkspace, AskName)

    async def test_rename_starts_from_the_current_name(self, home: Path) -> None:
        screen = RenameWorkspace("old")
        async with opened(screen) as (_app, _pilot, _answers):
            assert screen.query_one(Input).value == "old"

    async def test_renaming_to_a_name_already_taken_is_refused(self, home: Path) -> None:
        """Not relaxed for rename: two workspaces under one filename would lose one of them."""
        workspaces.save(workspaces.from_members("old", []))
        workspaces.save(workspaces.from_members("taken", []))
        screen = RenameWorkspace("old")
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(Input).value = "taken"
            await pilot.press("enter")
            await settle(pilot)

            assert answers == []
            assert "already exists" in error_of(screen)

    async def test_renaming_a_workspace_to_its_own_name_is_accepted(self, home: Path) -> None:
        """The box opens prefilled with the current name, so enter on an unchanged box is doing nothing wrong and must not be answered with 'already exists'."""
        workspaces.save(workspaces.from_members("old", []))
        screen = RenameWorkspace("old")
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("enter")
            await settle(pilot)

            assert answers == ["old"], "accepted and closed; the list is what decides a rename onto itself is a no-op"

    async def test_the_exemption_covers_only_the_name_being_renamed(self, home: Path) -> None:
        """A save box has nothing to be exempt from, so the same input it accepts on rename is still a collision here."""
        workspaces.save(workspaces.from_members("old", []))
        screen = NameWorkspace(1, "old")
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("enter")
            await settle(pilot)

            assert answers == []
            assert "already exists" in error_of(screen)


class TestConfirmIsTwoAnswersAndNothingElse:
    async def test_the_confirm_button_answers_true(self) -> None:
        screen = Confirm("Move 'w' to the trash?")
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one("#confirm", Button).press()
            await settle(pilot)

            assert answers == [True]

    async def test_the_cancel_button_answers_false(self) -> None:
        screen = Confirm("Move 'w' to the trash?")
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one("#cancel", Button).press()
            await settle(pilot)

            assert answers == [False]

    async def test_escape_answers_false(self) -> None:
        """Never `None`: the caller branches on the answer, and a falsy absence would read as a refusal by accident rather than by decision."""
        screen = Confirm("Move 'w' to the trash?")
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("escape")
            await settle(pilot)

            assert answers == [False]

    async def test_it_opens_on_the_safe_answer_and_the_arrows_reach_the_other(self) -> None:
        screen = Confirm("Move 'w' to the trash?")
        async with opened(screen) as (app, pilot, _answers):
            assert app.focused is not None and app.focused.id == "cancel"
            await pilot.press("right")
            assert app.focused is not None and app.focused.id == "confirm"
            await pilot.press("left")
            assert app.focused is not None and app.focused.id == "cancel"
            await pilot.press("down")
            assert app.focused is not None and app.focused.id == "confirm"
            await pilot.press("up")
            assert app.focused is not None and app.focused.id == "cancel"

    async def test_enter_on_the_focused_button_is_the_whole_answer(self) -> None:
        screen = Confirm("Move 'w' to the trash?")
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("right")
            await pilot.press("enter")
            await settle(pilot)

            assert answers == [True]

    async def test_the_button_says_what_will_happen(self) -> None:
        screen = Confirm("Move 'w' to the trash?", confirm="Open 20 panes")
        async with opened(screen) as (_app, _pilot, _answers):
            assert str(screen.query_one("#confirm", Button).label) == "Open 20 panes"

    async def test_detail_is_only_on_screen_when_there_is_some(self) -> None:
        bare = Confirm("Move 'w' to the trash?")
        async with opened(bare) as (_app, _pilot, _answers):
            assert len(bare.query("#hint")) == 0

        detailed = Confirm("Move 'w' to the trash?", "It holds 3 sessions.")
        async with opened(detailed) as (_app, _pilot, _answers):
            assert "3 sessions" in str(detailed.query_one("#hint", Label).render())


class TestWorkspacePickerReturnsAnActionAndAName:
    @pytest.mark.parametrize(("key", "action"), [("o", "restore"), ("f", "refresh"), ("e", "members"), ("r", "rename"), ("d", "delete")])
    async def test_every_advertised_key_answers_with_its_own_action(self, key: str, action: str) -> None:
        screen = WorkspacePicker([workspace("alpha", members=2), workspace("beta")])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press(key)
            await settle(pilot)

            assert answers == [(action, "alpha")], "the cursor starts on the first row"

    async def test_the_answer_follows_the_cursor(self) -> None:
        screen = WorkspacePicker([workspace("alpha"), workspace("beta")])
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(DataTable).move_cursor(row=1)
            await pilot.press("o")
            await settle(pilot)

            assert answers == [("restore", "beta")]

    async def test_selecting_a_row_restores_it(self) -> None:
        """Enter on a row is the same thing as pressing o, because a list you can move a cursor around invites it."""
        screen = WorkspacePicker([workspace("alpha")])
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(DataTable).focus()
            await pilot.press("enter")
            await settle(pilot)

            assert answers == [("restore", "alpha")]

    @pytest.mark.parametrize("key", ["escape", "q"])
    async def test_closing_answers_none(self, key: str) -> None:
        screen = WorkspacePicker([workspace("alpha")])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press(key)
            await settle(pilot)

            assert answers == [None]

    async def test_a_clipped_name_on_screen_is_still_answered_in_full(self) -> None:
        """The cell is cut to fit the column; the name that comes back has to be the one on disk."""
        long_name = "a" * 40
        screen = WorkspacePicker([workspace(long_name)])
        async with opened(screen) as (_app, pilot, answers):
            assert cells(screen)[0][0] == "a" * 34
            await pilot.press("o")
            await settle(pilot)

            assert answers == [("restore", long_name)]

    async def test_the_row_says_how_many_sessions_are_in_the_set(self) -> None:
        screen = WorkspacePicker([workspace("alpha", members=3)])
        async with opened(screen) as (_app, _pilot, _answers):
            assert cells(screen)[0][1] == "3"

    async def test_a_long_list_cannot_push_the_box_off_the_screen(self) -> None:
        screen = WorkspacePicker([workspace(f"w{index:02d}") for index in range(MAX_TABLE_ROWS + 10)])
        async with opened(screen) as (_app, _pilot, _answers):
            height = screen.query_one(DataTable).styles.height

            assert height is not None and height.value == MAX_TABLE_ROWS


class TestWorkspacePickerWithNothingSaved:
    async def test_an_action_key_does_nothing_rather_than_answering(self) -> None:
        screen = WorkspacePicker([])
        async with opened(screen) as (_app, pilot, answers):
            for key in ("o", "f", "e", "r", "d"):
                await pilot.press(key)
            await settle(pilot)

            assert answers == [], "there is no row to act on, and an action on nothing would crash the caller"

    async def test_it_says_how_to_make_one(self) -> None:
        screen = WorkspacePicker([])
        async with opened(screen) as (_app, _pilot, _answers):
            assert "Press s" in str(screen.query_one("#empty", Label).render())
            assert len(screen.query("DataTable")) == 0, "an empty picker offers a message and a way out, not an empty grid"

    async def test_the_close_button_answers_none(self) -> None:
        screen = WorkspacePicker([])
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one("#cancel", Button).press()
            await settle(pilot)

            assert answers == [None]


class TestWorkspaceMembersReturnsAnEditRatherThanPerformingOne:
    async def test_dropping_answers_with_the_session_under_the_cursor(self) -> None:
        screen = WorkspaceMembers("w", [entry("one"), entry("two")])
        async with opened(screen) as (_app, pilot, answers):
            screen.query_one(DataTable).move_cursor(row=1)
            await pilot.press("x")
            await settle(pilot)

            assert answers == [("drop", ["two"])]

    async def test_adding_answers_with_no_ids_because_the_next_screen_picks_them(self) -> None:
        screen = WorkspaceMembers("w", [entry("one")])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("a")
            await settle(pilot)

            assert answers == [("add", [])]

    @pytest.mark.parametrize("key", ["escape", "q"])
    async def test_closing_answers_none(self, key: str) -> None:
        screen = WorkspaceMembers("w", [entry("one")])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press(key)
            await settle(pilot)

            assert answers == [None]

    async def test_a_dead_member_is_named_as_such_rather_than_hidden(self) -> None:
        """Dropping it is the user's call, so it has to stay on the list and say what is wrong with it."""
        screen = WorkspaceMembers("w", [entry("gone", transcript=False)])
        async with opened(screen) as (_app, _pilot, _answers):
            assert cells(screen)[0][3] == "transcript gone"

    async def test_a_running_member_says_so(self) -> None:
        screen = WorkspaceMembers("w", [entry("busy", live=True), entry("idle")])
        async with opened(screen) as (_app, _pilot, _answers):
            assert [line[3] for line in cells(screen)] == ["running", ""]

    async def test_a_dead_member_can_still_be_dropped(self) -> None:
        """It is the only thing a user can usefully do with one, so the row must not be inert."""
        screen = WorkspaceMembers("w", [entry("gone", transcript=False)])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("x")
            await settle(pilot)

            assert answers == [("drop", ["gone"])]

    async def test_an_empty_set_still_offers_the_way_to_fill_it(self) -> None:
        screen = WorkspaceMembers("w", [])
        async with opened(screen) as (_app, pilot, answers):
            assert "Press a" in str(screen.query_one("#empty", Label).render())
            await pilot.press("x")
            await settle(pilot)
            assert answers == [], "there is nothing to drop"

            await pilot.press("a")
            await settle(pilot)
            assert answers == [("add", [])]

    async def test_a_long_set_cannot_push_the_box_off_the_screen(self) -> None:
        screen = WorkspaceMembers("w", [entry(f"s{index:02d}") for index in range(MAX_TABLE_ROWS + 10)])
        async with opened(screen) as (_app, _pilot, _answers):
            height = screen.query_one(DataTable).styles.height

            assert height is not None and height.value == MAX_TABLE_ROWS


class TestAddSessionsChecksNothingByDefault:
    async def test_nothing_is_checked_when_it_opens(self) -> None:
        """Adding to a saved set is a deliberate edit; a pre-ticked list would make it the default."""
        screen = AddSessions("w", [row("one"), row("two")])
        async with opened(screen) as (_app, _pilot, _answers):
            assert list(screen.query_one(SelectionList).selected) == []

    async def test_confirming_with_nothing_checked_says_so_instead_of_answering(self) -> None:
        screen = AddSessions("w", [row("one")])
        async with opened(screen) as (app, pilot, answers):
            await pilot.press("a")
            await settle(pilot)

            assert answers == [], "an empty add would be a no-op the caller cannot distinguish from a cancel"
            assert any("Nothing is checked" in notification.message for notification in app._notifications)

    async def test_space_checks_the_row_under_the_cursor_and_a_answers_with_it(self) -> None:
        screen = AddSessions("w", [row("one"), row("two")])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press("space")
            await pilot.press("a")
            await settle(pilot)

            assert answers == [["one"]]

    async def test_two_checked_sessions_both_come_back(self) -> None:
        screen = AddSessions("w", [row("one"), row("two"), row("three")])
        async with opened(screen) as (_app, pilot, answers):
            selection = screen.query_one(SelectionList)
            selection.select(selection.get_option_at_index(0))
            selection.select(selection.get_option_at_index(2))
            await pilot.press("a")
            await settle(pilot)

            assert answers == [["one", "three"]]

    async def test_space_twice_unchecks_again(self) -> None:
        screen = AddSessions("w", [row("one")])
        async with opened(screen) as (app, pilot, answers):
            await pilot.press("space")
            await pilot.press("space")
            await pilot.press("a")
            await settle(pilot)

            assert answers == []
            assert any("Nothing is checked" in notification.message for notification in app._notifications)

    @pytest.mark.parametrize("key", ["escape", "q"])
    async def test_closing_answers_none(self, key: str) -> None:
        screen = AddSessions("w", [row("one")])
        async with opened(screen) as (_app, pilot, answers):
            await pilot.press(key)
            await settle(pilot)

            assert answers == [None]

    async def test_with_nothing_left_to_add_it_says_so_and_answers_nothing(self) -> None:
        screen = AddSessions("w", [])
        async with opened(screen) as (_app, pilot, answers):
            assert "already in this set" in str(screen.query_one("#empty", Label).render())
            await pilot.press("space")
            await pilot.press("a")
            await settle(pilot)

            assert answers == []

            screen.query_one("#cancel", Button).press()
            await settle(pilot)
            assert answers == [None]
