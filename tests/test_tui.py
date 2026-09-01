"""The session list. Nothing is reachable by keyboard only, so what the footer and palette advertise has to actually work."""

import asyncio
from pathlib import Path

import pytest
from textual import events
from textual.widgets import DataTable, Footer, Input, Label, Static

from claude_code_workspaces import keep, launcher, live, restore, service, sessions, snapshot, tui, workspaces
from claude_code_workspaces.checklist import RestoreChecklist
from claude_code_workspaces.dialogs import AddSessions, Confirm, Dialog, NameWorkspace, RenameWorkspace, WorkspaceMembers, WorkspacePicker
from claude_code_workspaces.formatting import clip
from claude_code_workspaces.tui import SessionList, _row_style
from conftest import conversation, live_session, make_repo, write_claude_config, write_transcript


@pytest.fixture
def world(home: Path, monkeypatch: pytest.MonkeyPatch, set_live):
    """Two historical sessions and a switch for what is running."""
    root = make_repo(home / "code" / "app")
    write_claude_config(home, {root: True})
    write_transcript(home, "hist1", root, conversation("hist1", root, title="Login bug"))
    write_transcript(home, "hist2", root, conversation("hist2", root, title="Rate limits"))
    return root, set_live


async def screen_of[T](app: SessionList, pilot, kind: type[T]) -> T:
    """Wait for a modal to arrive. The actions that push one run in a worker, so the push lands a beat after the keypress."""
    for _ in range(50):
        if isinstance(app.screen, kind):
            return app.screen
        await pilot.pause()
        await asyncio.sleep(0.01)
    raise AssertionError(f"{kind.__name__} never appeared")


async def ready(app: SessionList, pilot) -> None:
    """The list loads on a worker, so a test has to wait for it the way a user waits for the first paint."""
    await app.workers.wait_for_complete()
    await pilot.pause()


def ids(app: SessionList) -> list[str]:
    return [row.session_id for row in app.filtered]


def drawn(app: SessionList) -> str:
    """What is actually composited onto the screen.

    Asserting on a widget's `render()` says only that it produced the text. The status line produced text for its whole existence while sitting in the same region as the Footer, which is docked there too and mounted after it, so none of it was ever visible. Anything that matters to a person reading the screen is checked here.
    """
    lines = ["".join(segment.text for segment in strip) for strip in app.screen._compositor.render_strips()]
    return "\n".join(lines)


async def test_the_list_shows_live_and_historical_together(world) -> None:
    root, set_live = world
    set_live(live_session("live1", root, name="app-eb"))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        assert sorted(ids(app)) == ["hist1", "hist2", "live1"]
        assert app.query_one(DataTable).row_count == 3


async def test_running_sessions_are_glyphed_and_sorted_first(world) -> None:
    root, set_live = world
    set_live(live_session("live1", root, status="waiting"))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        assert ids(app)[0] == "live1"
        assert app.filtered[0].glyph == "!"


async def test_search_narrows_the_table(world) -> None:
    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        app.query_one(Input).value = "rate"
        await pilot.pause()

        assert ids(app) == ["hist2"]
        assert app.query_one(DataTable).row_count == 1


async def test_pasting_a_session_id_lands_on_that_row(world) -> None:
    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        app.query_one(Input).value = "hist1"
        await pilot.pause()

        assert ids(app) == ["hist1"]


async def test_escape_clears_the_search(world) -> None:
    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        app.query_one(Input).value = "rate"
        await pilot.pause()
        await pilot.press("escape")

        assert app.query_one(Input).value == ""
        assert len(ids(app)) == 2


async def test_the_status_line_counts_what_is_on_screen(world) -> None:
    root, set_live = world
    set_live(live_session("live1", root, status="waiting"))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        line = str(app.query_one("#status", Static).render())

        assert "3 of 3 sessions" in line
        assert "1 live" in line
        assert "1 waiting" in line


async def test_copy_puts_the_resume_command_on_the_clipboard(world, monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr(SessionList, "copy_to_clipboard", lambda self, text: copied.append(text))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        table = app.query_one(DataTable)
        table.focus()
        await pilot.press("c")

    assert copied == [f"claude --resume {app.filtered[0].session_id}"]


async def test_resuming_opens_exactly_one_pane(world, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[dict] = []
    monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": launched.append({"groups": groups, "fork": fork}))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        app.query_one(DataTable).focus()
        await pilot.press("enter")

    assert len(launched) == 1
    assert sum(len(entries) for entries in launched[0]["groups"].values()) == 1
    assert launched[0]["fork"] is False


async def test_forking_asks_for_a_new_session_id(world, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[dict] = []
    monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": launched.append({"fork": fork}))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        app.query_one(DataTable).focus()
        await pilot.press("f")

    assert launched[0]["fork"] is True


async def test_a_running_session_is_not_reopened(world, monkeypatch: pytest.MonkeyPatch) -> None:
    root, set_live = world
    set_live(live_session("live1", root))
    launched: list[object] = []
    monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": launched.append(groups))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        table = app.query_one(DataTable)
        table.focus()
        table.move_cursor(row=0)  # the live one sorts first
        await pilot.press("enter")

    assert launched == []


async def test_a_session_flipping_to_waiting_raises_a_notification(world) -> None:
    root, set_live = world
    set_live(live_session("live1", root, status="busy"))

    app = SessionList()
    async with app.run_test(notifications=True) as pilot:
        await ready(app, pilot)
        set_live(live_session("live1", root, status="waiting"))
        app._apply_live(*live.try_live_sessions())
        await pilot.pause()

        assert any("waiting" in notification.message for notification in app._notifications)


async def test_a_session_already_waiting_is_not_announced_again(world) -> None:
    root, set_live = world
    set_live(live_session("live1", root, status="waiting"))

    app = SessionList()
    async with app.run_test(notifications=True) as pilot:
        await ready(app, pilot)
        app._notifications.clear()
        app._apply_live(*live.try_live_sessions())
        await pilot.pause()

        assert list(app._notifications) == []


async def test_opening_the_list_records_what_is_live(world) -> None:
    """There is no daemon, so the snapshot restore depends on is only written by runs like this one."""
    root, set_live = world
    set_live(live_session("live1", root))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)

    populated = snapshot.latest_populated()
    assert populated is not None
    assert [entry["sessionId"] for entry in populated[1]] == ["live1"]


async def test_the_list_reads_the_live_source_once_on_the_way_up(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """The snapshot used to spawn `claude agents --json` a second time for an answer the reload already had."""
    root, set_live = world
    set_live(live_session("live1", root))
    calls = 0
    answer = live.try_live_sessions

    def counted():
        nonlocal calls
        calls += 1
        return answer()

    monkeypatch.setattr(live, "try_live_sessions", counted)

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)

    assert calls == 1


async def test_a_snapshot_failure_never_disturbs_the_list(world) -> None:
    _, set_live = world
    set_live(unavailable="`claude` was not found on PATH.")

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)  # an empty live set is never written, and must not raise

        assert app.query_one(DataTable).row_count == 2


def test_a_long_name_is_clipped_with_a_visible_mark() -> None:
    """110 of 288 names were longer than the old fixed 44 characters, and were cut without saying so."""
    assert clip("short", 20) == "short"
    assert clip("x" * 40, 10) == "x" * 9 + "…"
    assert clip("anything", 0) == ""


async def test_the_name_column_fits_the_content_rather_than_the_terminal(world) -> None:
    """The longest name is 69 characters, so a 200-column terminal must not hand the column 90."""
    app = SessionList()
    async with app.run_test(size=(200, 30)) as pilot:
        await ready(app, pilot)
        wide = app._widths().name
        longest = max(len(row.label) for row in app.rows)

    assert wide == max(longest, 24)


async def test_a_narrow_terminal_still_leaves_the_name_readable(world) -> None:
    small = SessionList()
    async with small.run_test(size=(60, 30)) as pilot:
        await ready(small, pilot)
        widths = small._widths()

    assert widths.name >= 24
    assert widths.project >= len("Project")
    assert widths.branch >= len("Branch")


class TestWorkspacesInTheList:
    """The menu has to be able to do what the subcommands do, and through the same code."""

    async def test_saving_writes_the_same_workspace_the_cli_would(self, world, home: Path) -> None:
        root, set_live = world
        set_live(live_session("live1", root), live_session("live2", root))

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("s")
            screen = await screen_of(app, pilot, NameWorkspace)
            screen.query_one(Input).value = "api-refactor"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert workspaces.load("api-refactor").session_ids == ["live1", "live2"]

    async def test_an_unusable_name_is_refused_before_anything_is_written(self, world) -> None:
        root, set_live = world
        set_live(live_session("live1", root))

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("s")
            screen = await screen_of(app, pilot, NameWorkspace)
            screen.query_one(Input).value = "Bad Name"
            await pilot.press("enter")
            await pilot.pause()

            assert "not a usable workspace name" in str(screen.query_one("#error", Label).render())
            await pilot.press("escape")

        assert workspaces.load_all() == []

    async def test_saving_with_nothing_running_is_refused(self, world) -> None:
        app = SessionList()
        async with app.run_test(notifications=True) as pilot:
            await ready(app, pilot)
            await pilot.press("s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert any("nothing to save" in n.message for n in app._notifications)
        assert workspaces.load_all() == []

    async def test_the_workspace_screen_lists_what_the_cli_saved(self, world, home: Path) -> None:
        root, set_live = world
        set_live(live_session("live1", root))
        workspaces.save(workspaces.from_live("saved-elsewhere", [live_session("live1", root)]))

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("w")
            screen = await screen_of(app, pilot, WorkspacePicker)
            names = [w.name for w in screen.found]
            await pilot.press("escape")

        assert names == ["saved-elsewhere"]

    async def test_restoring_a_workspace_opens_its_sessions(self, world, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, set_live = world
        launched: list[dict] = []
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": launched.append(groups))
        workspaces.save(workspaces.from_live("w", [live_session("hist1", root), live_session("hist2", root)]))
        set_live()

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("w")
            await screen_of(app, pilot, WorkspacePicker)
            await pilot.press("o")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert len(launched) == 1
        assert sum(len(entries) for entries in launched[0].values()) == 2

    async def test_deleting_a_workspace_asks_first_and_moves_it_to_the_trash(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("w")
            await screen_of(app, pilot, WorkspacePicker)
            await pilot.press("d")
            await screen_of(app, pilot, Confirm)
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert workspaces.exists("w")


class TestConfirmDialog:
    """Two buttons side by side, so the arrow keys have to move between them. Tab alone did, and that is not what anyone reaches for."""

    async def confirm(self, pilot, app) -> Confirm:
        for _ in range(60):
            if isinstance(app.screen, Confirm):
                return app.screen
            await pilot.pause()
            await asyncio.sleep(0.01)
        raise AssertionError("Confirm never appeared")

    async def open_it(self, app, pilot) -> Confirm:
        await ready(app, pilot)
        await pilot.press("w")
        await screen_of(app, pilot, WorkspacePicker)
        await pilot.press("d")
        return await self.confirm(pilot, app)

    async def test_it_opens_on_the_safe_answer(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))
        app = SessionList()
        async with app.run_test() as pilot:
            await self.open_it(app, pilot)
            assert app.focused is not None
            assert app.focused.id == "cancel"
            await pilot.press("escape")

    async def test_an_arrow_reaches_the_other_button(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))
        app = SessionList()
        async with app.run_test() as pilot:
            await self.open_it(app, pilot)
            await pilot.press("right")
            assert app.focused is not None and app.focused.id == "confirm"
            await pilot.press("left")
            assert app.focused is not None and app.focused.id == "cancel"
            await pilot.press("escape")

    async def test_confirming_moves_the_workspace_to_the_trash(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))
        app = SessionList()
        async with app.run_test() as pilot:
            await self.open_it(app, pilot)
            await pilot.press("right")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert not workspaces.exists("w")
        assert list((home / ".ccw" / "trash").glob("w.*.json"))


async def press_and_watch(app, pilot, node, binding_key: str, action: str) -> bool:
    """Press a key and report whether its action ran, with the action itself stubbed out so nothing happens.

    Actions are resolved as `action_<name>` on the node, so replacing that attribute intercepts the call without touching the binding.
    """
    name = f"action_{action.split('(', maxsplit=1)[0]}"
    original = getattr(node, name, None)
    if original is None:
        raise AssertionError(f"{type(node).__name__} advertises {binding_key!r} but has no {name}")
    fired: list[str] = []
    setattr(node, name, lambda *_args, **_kwargs: fired.append(action))
    try:
        await pilot.press(binding_key)
        await pilot.pause()
    finally:
        setattr(node, name, original)
    return bool(fired)


def advertised(node) -> list[tuple[str, str]]:
    """Every key a screen's own bindings claim, one entry per key in a comma-separated binding."""
    claimed: list[tuple[str, str]] = []
    for binding in type(node).BINDINGS:
        keys, action = (binding.key, binding.action) if hasattr(binding, "key") else (binding[0], binding[1])
        claimed.extend((key, action) for key in keys.split(","))
    return claimed


class TestEveryAdvertisedKeyWorks:
    """Three separate bugs had the same shape: a footer promised a key, and something swallowed it first.

    `enter` on the restore checklist, `enter` on the session list, and the arrow keys on the confirm dialog. This checks the property directly, for every key on every screen, in the focus state the screen actually opens in. Each key gets a freshly opened screen, because a key that works may also close it.
    """

    async def dead_keys(self, world, open_screen) -> list[str]:
        dead: list[str] = []
        probe = SessionList()
        async with probe.run_test() as pilot:
            await ready(probe, pilot)
            node = await open_screen(probe, pilot)
            keys = advertised(node)
        for key, action in keys:
            app = SessionList()
            async with app.run_test() as pilot:
                await ready(app, pilot)
                node = await open_screen(app, pilot)
                if not await press_and_watch(app, pilot, node, key, action):
                    dead.append(key)
        return dead

    async def test_the_session_list(self, world) -> None:
        async def here(app, pilot):
            return app

        assert await self.dead_keys(world, here) == []

    async def test_the_workspace_box(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))

        async def open_box(app, pilot):
            await pilot.press("w")
            return await screen_of(app, pilot, WorkspacePicker)

        assert await self.dead_keys(world, open_box) == []

    async def test_the_confirm_dialog(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))

        async def open_confirm(app, pilot):
            await pilot.press("w")
            await screen_of(app, pilot, WorkspacePicker)
            await pilot.press("d")
            return await screen_of(app, pilot, Confirm)

        assert await self.dead_keys(world, open_confirm) == []

    async def test_the_name_box(self, world) -> None:
        root, set_live = world
        set_live(live_session("live1", root))

        async def open_name(app, pilot):
            await pilot.press("s")
            return await screen_of(app, pilot, NameWorkspace)

        assert await self.dead_keys(world, open_name) == []


class TestSuggestedName:
    """A default is only useful when it is right. A wrong one is worse than an empty box, because it gets accepted."""

    def row(self, project: str) -> sessions.SessionRow:
        root = Path("C:/code") / project
        return sessions.SessionRow(session_id="s", cwd=root, root=root, title=None, agent_name=None, archived=False, status="idle", last_active=None, transcript=None, branch=None, first_prompt=None)

    def test_one_project_suggests_its_name(self, home: Path) -> None:
        assert tui._suggested_name([self.row("api"), self.row("api")]) == "api"

    def test_several_projects_suggest_nothing(self, home: Path) -> None:
        assert tui._suggested_name([self.row("api"), self.row("worker")]) == ""

    def test_a_project_name_is_made_usable_as_a_workspace_name(self, home: Path) -> None:
        assert tui._suggested_name([self.row("Acme.Tools Sandbox")]) == "acme.tools-sandbox"

    def test_a_name_already_taken_is_not_suggested(self, home: Path) -> None:
        workspaces.save(workspaces.from_live("api", []))

        assert tui._suggested_name([self.row("api")]) == ""

    @pytest.mark.parametrize("project", ["_foo", "___", "x" * 80, "-", "%%%"])
    def test_a_project_name_that_cannot_become_one_suggests_nothing(self, home: Path, project: str) -> None:
        """`workspaces.exists` validates, so a derived name the pattern refuses used to raise out of the save worker and take the dialog with it."""
        assert tui._suggested_name([self.row(project)]) == ""

    async def test_the_save_dialog_still_opens_for_an_underivable_project_name(self, home: Path, monkeypatch: pytest.MonkeyPatch, set_live) -> None:
        root = make_repo(home / "code" / "_foo")
        write_claude_config(home, {root: True})
        set_live(live_session("live1", root))

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("s")
            screen = await screen_of(app, pilot, NameWorkspace)

            assert screen.query_one(Input).value == "", "an underivable name leaves the box empty rather than crashing the worker"
            await pilot.press("escape")


class TestDialogLayout:
    """A dialog is as tall as what is in it. `Vertical` and `Horizontal` default to `height: 1fr`, which no rule from a subclass or screen could beat, and every box came out stretched to most of the terminal."""

    async def box_of(self, app, pilot, cls):
        screen = await screen_of(app, pilot, cls)
        return screen.query_one("#box")

    async def test_the_name_box_fits_its_content(self, world) -> None:
        root, set_live = world
        set_live(live_session("live1", root))
        app = SessionList()
        async with app.run_test(size=(155, 40)) as pilot:
            await ready(app, pilot)
            await pilot.press("s")
            box = await self.box_of(app, pilot, NameWorkspace)

            # The property, not an arithmetic tolerance: `1fr` here is what stretched every dialog.
            assert box.styles.height is not None and box.styles.height.is_auto
            assert box.region.height < 20
            await pilot.press("escape")

    async def test_the_confirm_box_fits_its_content(self, world, home: Path) -> None:
        workspaces.save(workspaces.from_live("w", [live_session("hist1", home / "code" / "app")]))
        app = SessionList()
        async with app.run_test(size=(155, 40)) as pilot:
            await ready(app, pilot)
            await pilot.press("w")
            await screen_of(app, pilot, WorkspacePicker)
            await pilot.press("d")
            box = await self.box_of(app, pilot, Confirm)

            assert box.styles.height is not None and box.styles.height.is_auto
            assert box.region.height < 14
            await pilot.press("escape")

    async def test_every_dialog_uses_the_same_shell(self, world, home: Path) -> None:
        """One border, one padding, one button row, so the boxes read as one tool."""
        for screen in (NameWorkspace(1), Confirm("q"), WorkspacePicker([])):
            assert isinstance(screen, Dialog)


class TestColumnHeaders:
    """A header wider than its column is silently cut, and `Last active` in a nine-wide column read as `Last act`."""

    @pytest.mark.parametrize("width", [70, 77, 100, 155, 220])
    async def test_no_header_is_cut_at_any_terminal_width(self, world, width: int) -> None:
        app = SessionList()
        async with app.run_test(size=(width, 30)) as pilot:
            await ready(app, pilot)
            table = app.query_one(DataTable)
            cut = [str(c.label) for c in table.columns.values() if c.width and len(str(c.label)) > c.width]

        assert cut == [], f"cut at {width} columns: {cut}"


async def test_resuming_from_the_list_opens_a_tab_not_a_window(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening a whole window for one conversation is heavy; `ccw restore` is the one that earns a window."""
    windows: list[str] = []
    monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": windows.append(window))

    app = SessionList()
    async with app.run_test() as pilot:
        await ready(app, pilot)
        await pilot.press("o")

    assert windows == [launcher.CURRENT_WINDOW]


class TestColumnBudget:
    """The columns have to fit the room that is actually there, which is not the width of the widget."""

    @pytest.mark.parametrize("width", [80, 100, 118, 155, 220])
    async def test_the_columns_fit_the_scrollable_area(self, world, width: int) -> None:
        """A vertical scrollbar takes two columns. Spending them anyway cut `Last seen` down to `Last see`."""
        app = SessionList()
        async with app.run_test(size=(width, 12)) as pilot:
            await ready(app, pilot)
            table = app.query_one(DataTable)
            widths = app._widths()
            available = table.scrollable_content_region.width or table.size.width
            asked = tui.GLYPH_WIDTH + widths.name + widths.project + widths.branch + tui.AGE_WIDTH + tui._CELL_PADDING * tui._COLUMN_COUNT

        assert asked <= available or widths.name == tui.MIN_NAME_WIDTH, f"{asked} asked of {available} at {width} columns"

    async def test_the_branch_column_shrinks_to_its_content(self, world) -> None:
        """Branches are `main`, `master` and `PROJ-2116` almost every time, and a fixed sixteen made every row pay for the two that are not."""
        app = SessionList()
        async with app.run_test(size=(155, 30)) as pilot:
            await ready(app, pilot)
            widths = app._widths()

        assert widths.branch <= tui.MAX_BRANCH_WIDTH
        assert widths.branch >= len("Branch")


class TestArchiveInTheList:
    async def test_an_archived_session_leaves_the_list_but_not_the_disk(self, world, home: Path) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[0]
            await pilot.press("a")
            await pilot.pause()

            assert target.session_id not in [row.session_id for row in app.filtered]
            assert target.transcript is not None and target.transcript.exists()

    async def test_the_status_line_says_how_many_are_put_away(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("a")
            await pilot.pause()

            assert "1 archived" in str(app.query_one("#status", Static).render())

    async def test_they_can_be_brought_back_into_view(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            before = len(app.filtered)
            await pilot.press("a")
            await pilot.pause()
            assert len(app.filtered) == before - 1

            await pilot.press("h")
            await pilot.pause()
            assert len(app.filtered) == before
            assert "shown" in str(app.query_one("#status", Static).render())

    async def test_archiving_the_same_session_twice_unarchives_it(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            before = len(app.filtered)
            target = app.filtered[0].session_id
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("h")  # show them again so the row can be selected
            await pilot.pause()
            app._move_cursor_to(target)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("h")

            assert len(app.filtered) == before


class TestPollingBacksOff:
    """Twelve `claude agents --json` processes a minute is real battery for an answer nobody is looking at."""

    async def test_nothing_is_polled_while_the_window_is_not_focused(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        polls: list[int] = []
        monkeypatch.setattr(SessionList, "_poll_worker", lambda _self: polls.append(1))
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            app.post_message(events.AppBlur())
            await pilot.pause()
            app._poll()
            await pilot.pause()

            assert polls == []

    async def test_coming_back_catches_up_at_once(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """What is on screen must never be older than the moment you looked at it."""
        polls: list[int] = []
        monkeypatch.setattr(SessionList, "_poll_worker", lambda _self: polls.append(1))
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            app.post_message(events.AppBlur())
            await pilot.pause()
            app.post_message(events.AppFocus())
            await pilot.pause()
            await asyncio.sleep(0.05)
            await pilot.pause()

            assert len(polls) == 1

    async def test_the_footer_still_fits_after_the_archive_keys(self, world) -> None:
        app = SessionList()
        async with app.run_test(size=(118, 30)) as pilot:
            await ready(app, pilot)
            footer = app.query_one(Footer)
            wanted = sum(key.size.width for key in footer.query("FooterKey"))

            assert wanted <= footer.size.width, f"the footer wants {wanted} of {footer.size.width}"


class TestTheListSurvivesAFailure:
    """Every one of these took the whole app down with a traceback before the outcomes went through `service`."""

    async def test_a_workspace_that_moved_under_the_list_is_a_notification(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        def gone(_name):
            raise workspaces.WorkspaceError("No workspace called 'gone'.")

        app = SessionList()
        async with app.run_test(notifications=True) as pilot:
            await ready(app, pilot)
            monkeypatch.setattr(workspaces, "load", gone)

            await app._restore_workspace("gone")
            await pilot.pause()

            assert app.is_running
            assert any("gone" in notification.message for notification in app._notifications)

    async def test_an_archive_that_cannot_be_written_leaves_the_row_alone(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        app = SessionList()
        async with app.run_test(notifications=True) as pilot:
            await ready(app, pilot)
            monkeypatch.setattr(tui.service.archive, "_write", lambda _ids: False)
            app.query_one(DataTable).focus()
            await pilot.press("a")
            await pilot.pause()

            assert app.is_running
            assert not any(row.archived for row in app.rows)


class TestTheFirstPaintDoesNotWaitForClaude:
    async def test_transcripts_are_on_screen_before_liveness_is_known(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """The local half costs 0.68s and the live half 1.24s. Waiting for both is what read as a frozen window."""
        rows_when_live_arrived: list[int] = []
        fold_in = SessionList._apply_live

        def record(self, live, note):
            rows_when_live_arrived.append(self.query_one(DataTable).row_count)
            fold_in(self, live, note)

        monkeypatch.setattr(SessionList, "_apply_live", record)

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

        assert rows_when_live_arrived == [2], "the transcripts must already be drawn when the live read comes back"

    async def test_the_status_line_does_not_claim_nothing_is_running_yet(self, world) -> None:
        """Between the two paints the rows are up but liveness is not in yet, and "0 live" there would be a claim rather than a gap."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            app._awaiting_live = True
            app._apply_history(sessions.history_rows())
            await pilot.pause()
            line = str(app.query_one("#status", Static).render())

            assert "checking what is running" in line
            assert "0 live" not in line


class TestDegradationIsVisible:
    async def test_an_unreadable_transcript_is_reported_in_the_status_line(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """The tool keeps working on live data alone, and the list quietly gets shorter. Saying so is the difference between degrading and lying."""
        monkeypatch.setattr(tui.transcripts, "errors", lambda: ["broken.jsonl: JSONDecodeError: line 1"])

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            assert "1 transcript(s) unreadable" in str(app.query_one("#status", Static).render())


class TestSearchIsAskedForRatherThanOffered:
    """A visible but unfocused box invited typing, and every letter is a binding: "res" reloaded on `r` and opened the save dialog on `s`."""

    async def test_the_box_is_not_on_screen_until_it_is_wanted(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            assert app.query_one("#search", Input).display is False
            assert isinstance(app.focused, DataTable)

    async def test_slash_opens_it_with_the_caret_in_it(self, world) -> None:
        """Focus comes with it, so the next keystroke is the search rather than a binding."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("slash")

            search = app.query_one("#search", Input)
            assert search.display is True
            assert search.has_focus

    async def test_typing_a_word_that_is_all_bindings_reaches_the_box(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("slash")
            for character in "res":
                await pilot.press(character)
            await pilot.pause()

            assert app.query_one("#search", Input).value == "res"
            assert app.screen.__class__.__name__ == "Screen", "no dialog may open while the search box has focus"

    async def test_it_stays_on_screen_while_it_holds_a_filter(self, world) -> None:
        """A short list with nothing to explain it reads as a short list, not a filtered one."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("slash")
            app.query_one("#search", Input).value = "rate"
            await pilot.pause()
            app.query_one(DataTable).focus()
            await pilot.pause()
            app._repaint()

            assert ids(app) == ["hist2"]
            assert app.query_one("#search", Input).display is True

    async def test_escape_clears_it_and_puts_it_away(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await pilot.press("slash")
            app.query_one("#search", Input).value = "rate"
            await pilot.pause()
            await pilot.press("escape")

            assert app.query_one("#search", Input).display is False
            assert len(ids(app)) == 2

    async def test_the_title_is_chosen_rather_than_inherited_from_the_class_name(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            assert app.title == "Claude Code Workspaces"

    async def test_it_is_hidden_from_the_very_first_frame(self, world) -> None:
        """Composing it visible and taking it away in `on_mount` left it on screen for the length of the first paint, which is seconds on a cold store."""
        app = SessionList()
        async with app.run_test() as pilot:
            assert app.query_one("#search", Input).display is False, "visible before the list has even loaded"
            await ready(app, pilot)
            assert app.query_one("#search", Input).display is False


class TestFocusIsDeclaredNotInferred:
    """Two bugs came out of leaving focus to Textual's default and then reading it back.

    `AUTO_FOCUS` defaults to `"*"`, and `Widget.focusable` tests `visible`, not `display` — a box hidden with `display: none` is still a focus candidate. It got focused during the first load, and visibility derived from focus put it on screen for the whole load.
    """

    async def test_the_search_box_never_takes_focus_on_its_own(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            search = app.query_one("#search", Input)
            assert not search.has_focus
            await ready(app, pilot)
            assert not search.has_focus
            assert isinstance(app.focused, DataTable)

    async def test_visibility_follows_the_state_and_not_the_focus(self, world) -> None:
        """`searching` is the only thing that decides, so nothing that touches focus can reveal the box."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            search = app.query_one("#search", Input)

            search.focus()
            await pilot.pause()
            assert search.display is False, "focus alone must not put it on screen"

            app.searching = True
            await pilot.pause()
            assert search.display is True

    async def test_every_dialog_states_what_it_focuses(self) -> None:
        """The app's selector names a widget no dialog contains, so inheriting it left them with nothing focused."""
        assert Dialog.AUTO_FOCUS == "#cancel"
        assert NameWorkspace.AUTO_FOCUS == "#name"
        assert WorkspacePicker.AUTO_FOCUS == "DataTable"
        assert SessionList.AUTO_FOCUS == "DataTable"


class TestArchivingSaysHowToUndoIt:
    async def test_the_footer_carries_the_way_back(self, world) -> None:
        """The way back has to be somewhere permanent rather than in a notification that fades, and on a key that is not one shift away from the thing it undoes."""
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)

            assert "Hidden" in drawn(app)

    async def test_the_status_line_says_only_what_is_on_screen(self, world) -> None:
        """State above, actions below. An instruction in the counts line made the two bottom rows compete rather than complement."""
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            await pilot.press("a")
            await pilot.pause()
            line = str(app.query_one("#status", Static).render())

            assert "1 archived" in line
            assert "shift" not in line and "to show" not in line


class TestTheStatusLineIsOnScreen:
    """It was docked into the Footer's region and covered by it, so every count and warning it carried was invisible."""

    async def test_the_counts_are_visible(self, world) -> None:
        root, set_live = world
        set_live(live_session("live1", root, status="waiting"))
        app = SessionList()
        async with app.run_test(size=(120, 20)) as pilot:
            await ready(app, pilot)

            assert "3 of 3 sessions" in drawn(app)
            assert "1 waiting" in drawn(app)

    async def test_the_archived_count_is_visible(self, world) -> None:
        app = SessionList()
        async with app.run_test(size=(120, 20)) as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            await pilot.press("a")
            await pilot.pause()

            assert "1 archived" in drawn(app)

    async def test_it_does_not_sit_on_top_of_the_footer(self, world) -> None:
        app = SessionList()
        async with app.run_test(size=(120, 20)) as pilot:
            await ready(app, pilot)

            assert app.query_one("#status", Static).region != app.query_one(Footer).region
            assert "Resume" in drawn(app), "the footer must still be there too"


class TestTheViewStaysWhereYouPutIt:
    async def test_scrolling_away_survives_a_poll(self, world, home: Path) -> None:
        """The poll repaints every five seconds, and rebuilding the table reset the scroll, so the screen appeared to jump on its own."""
        root, _ = world
        for index in range(60):
            write_transcript(home, f"s{index:02d}", root, conversation(f"s{index:02d}", root))
        app = SessionList()
        async with app.run_test(size=(120, 20)) as pilot:
            await ready(app, pilot)
            table = app.query_one(DataTable)
            table.scroll_to(y=30, animate=False)
            await pilot.pause()
            parked = table.scroll_offset.y
            assert parked > 0, "the fixture must be tall enough to scroll"

            app._repaint()
            await pilot.pause()

            assert table.scroll_offset.y == parked

    async def test_an_archived_row_is_dimmed_rather_than_marked(self, world) -> None:
        """One channel is enough, and no new glyph competes with the status column."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            await pilot.press("a")
            await pilot.press("h")
            await pilot.pause()

            archived = next(row for row in app.filtered if row.archived)
            assert tui._row_style(archived, {"waiting": "yellow", "busy": "green", "idle": "blue"}) == "dim"

    async def test_a_session_waiting_on_you_takes_the_warning_colour(self, world) -> None:
        """The one thing worth scanning 294 rows for. The glyph still carries it, so colour is never the only carrier."""
        root, set_live = world
        set_live(live_session("live1", root, status="waiting"))
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            palette = {"waiting": "yellow", "busy": "green", "idle": "blue"}
            waiting = next(row for row in app.filtered if row.status == "waiting")
            assert tui._row_style(waiting, palette) == "yellow"
            assert tui._row_style(app.filtered[-1], palette) == "", "finished sessions keep the default"


class TestColourMeansSomethingNeedsYou:
    """The only colour on the screen belongs to the one row that is asking for something."""

    async def test_the_footer_does_not_compete_with_a_waiting_session(self, world) -> None:
        """Textual keys the footer in $warning, the same hue a waiting row takes, and ten permanent hints out-shouted one urgent row."""
        root, set_live = world
        set_live(live_session("live1", root, status="waiting"))
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)
            keys = app.query_one("FooterKey").get_component_styles("footer-key--key").color.hex.lower()
            warning = app.theme_variables["warning"].lower()
            waiting = app.theme_variables["text-warning"].lower()

            assert keys not in (warning, waiting), "the footer keys carry weight through bold, not through the hue that means 'this needs you'"

    async def test_no_header_takes_a_row_from_the_list(self, world) -> None:
        """It said the app's name to someone who had just typed `ccw` to open it."""
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)

            assert not app.query("Header")
            assert app.query_one(DataTable).region.y == 0
            assert app.title == "Claude Code Workspaces", "still set, because Textual uses it for the terminal title"


class TestArchivedNeverLooksLikeAnOrdinaryRow:
    """The reason the history is left at the default: dimming it would dim 288 of 294 rows and archived would stop being distinguishable, which is the only thing marking it."""

    async def test_the_four_states_are_all_distinct(self, world, home: Path) -> None:
        root, set_live = world
        write_transcript(home, "arch", root, conversation("arch", root, title="Put away"))
        set_live(live_session("wait1", root, status="waiting"), live_session("busy1", root, status="busy"))
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            service.archive_session("arch", "Put away")
            app.action_reload()
            await ready(app, pilot)
            await pilot.press("h")
            await pilot.pause()

            palette = app._palette()
            by_id = {row.session_id: tui._row_style(row, palette) for row in app.filtered}

            assert by_id["wait1"] == palette["waiting"]
            assert by_id["busy1"] == palette["busy"]
            assert by_id["hist1"] == "", "finished sessions keep the default"
            assert by_id["arch"] == "dim"
            assert len(set(by_id.values())) == 4, "every state has to look like itself"

    async def test_an_archived_row_renders_darker_than_an_ordinary_one(self, world, home: Path) -> None:
        """Measured rather than assumed: #9d9d9d against #e0e0e0."""
        app = SessionList()
        async with app.run_test(size=(100, 12)) as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            await pilot.press("a")
            await pilot.press("h")
            await pilot.pause()
            app.query_one(DataTable).move_cursor(row=1)  # off the archived row, so the cursor style is not what we measure
            await pilot.pause()

            shades = {}
            for strip in app.screen._compositor.render_strips():
                for segment in strip:
                    triplet = segment.style.color.triplet if segment.style and segment.style.color else None
                    if triplet is not None and segment.text.strip() in ("Login bug", "Rate limits"):
                        shades[segment.text.strip()] = triplet.red

            assert len(shades) == 2, f"both rows must be on screen, saw {shades}"
            assert min(shades.values()) < max(shades.values()), "archived and ordinary rows rendered the same shade"


class TestColourMeansTheSessionIsAlive:
    """A session open right now used to render in the same shade as one that finished months ago, with only the glyph between them."""

    async def test_every_running_status_gets_a_colour_and_nothing_else_does(self, world, home: Path) -> None:
        root, set_live = world
        set_live(live_session("w", root, status="waiting"), live_session("b", root, status="busy"), live_session("i", root, status="idle"))
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            palette = app._palette()
            styles = {row.session_id: tui._row_style(row, palette) for row in app.filtered}

            assert all(styles[session_id] for session_id in ("w", "b", "i")), "a live session always carries a colour"
            assert styles["hist1"] == "", "a finished session never does"
            assert len({styles["w"], styles["b"], styles["i"]}) == 3, "and each status is its own colour"

    async def test_no_two_states_share_a_colour(self, world) -> None:
        """`text-accent` resolves to the same value as `text-warning` in this theme, which is why it is not used."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            palette = app._palette()

            assert len(set(palette.values())) == len(palette)
            assert app.theme_variables["foreground"].lower() not in {colour.lower() for colour in palette.values()}


class TestTheListRestoresTheSameWayTheCommandLineDoes:
    async def test_restoring_a_workspace_records_the_set_it_rebuilt(self, world, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The command line snapshotted after a restore and the list did not, which is the drift that moving the launch into `service` closes."""
        root, set_live = world
        workspaces.save(workspaces.from_live("w", [live_session("hist2", root)]))
        # `hist2` is the one being reopened, so it must not be running; `hist1` is what the snapshot should then record.
        set_live(live_session("hist1", root))
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": None)
        monkeypatch.setattr(service, "SETTLE_SECONDS", 0)

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            await app._restore_workspace("w")
            await app.workers.wait_for_complete()
            await pilot.pause()

        populated = snapshot.latest_populated()
        assert populated is not None, "the list must record what it rebuilt, the way the command line does"
        assert [entry["sessionId"] for entry in populated[1]] == ["hist1"]

    async def test_resuming_goes_through_the_service(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """The refusals and the wording live in one place, so the two surfaces cannot answer differently."""
        asked: list[bool] = []
        monkeypatch.setattr(service, "resume_session", lambda row, *, fork=False: asked.append(fork) or service.Outcome(ok=True, message="ok"))

        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            await pilot.press("o")
            await pilot.press("f")
            await pilot.pause()

        assert asked == [False, True]


class TestTheCrashReflexIsInTheMenu:
    """A bare `ccw` is the menu and nobody has to learn a subcommand. Reopening what was running is the thing the tool exists for, and it used to be reachable only as `ccw restore`."""

    async def test_the_footer_offers_it(self, world) -> None:
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)

            assert "Previous" in drawn(app)

    async def test_it_asks_before_opening_anything(self, world, home: Path) -> None:
        """The plan includes a modification-time heuristic that is fuzzy at the edges, so this one always shows the checklist."""
        launched: list[object] = []
        app = SessionList()
        async with app.run_test(size=(118, 24)) as pilot:
            await ready(app, pilot)
            app.query_one(DataTable).focus()
            await pilot.press("p")
            screen = await screen_of(app, pilot, RestoreChecklist)

            assert screen is not None, "nothing may open before the checklist has been answered"
            assert launched == []
            await pilot.press("escape")


class TestAWorkspaceLargerThanTheCapAsksFirst:
    async def test_a_small_workspace_opens_without_asking(self, world, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A workspace is a decision already made, so the usual case does not ask."""
        root, _ = world
        workspaces.save(workspaces.from_live("small", [live_session("hist2", root)]))
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": None)
        monkeypatch.setattr(service, "SETTLE_SECONDS", 0)

        app = SessionList()
        async with app.run_test(notifications=True) as pilot:
            await ready(app, pilot)
            await app._restore_workspace("small")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert not isinstance(app.screen, Confirm)
            assert any("Opening" in notification.message for notification in app._notifications)

    async def test_more_panes_than_the_cap_needs_an_answer(self, world, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Past the pane cap, restore asks once before opening everything openable."""
        root, _ = world
        many = [f"s{index:02d}" for index in range(restore.DEFAULT_PANE_CAP + 4)]
        for session_id in many:
            write_transcript(home, session_id, root, conversation(session_id, root))
        workspaces.save(workspaces.from_live("big", [live_session(session_id, root) for session_id in many]))
        launched: list[object] = []
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": launched.append(groups))

        app = SessionList()
        async with app.run_test(size=(100, 30)) as pilot:
            await ready(app, pilot)
            # On a worker rather than awaited: the confirmation uses `push_screen_wait`, which Textual refuses outside one. In the app it is `action_workspaces` that supplies it.
            app.run_worker(app._restore_workspace("big"))
            confirm = await screen_of(app, pilot, Confirm)

            assert confirm is not None
            assert launched == [], "nothing opens until the question is answered"
            await pilot.press("escape")
            await pilot.pause()
            assert launched == []


class TestEveryActionIsOnScreen:
    """There is no command palette, so the footer is the whole answer to "what can this do". That only works while everything fits."""

    async def test_only_escape_is_left_off_the_footer(self, world) -> None:
        """`escape` clears the search and only means anything while a search is running, so it is explained where it applies rather than taking a permanent slot."""
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)
            # Textual's own bindings (ctrl+c and friends) are not ours to show; these are the ones this app declares.
            declared = {binding.key for binding in SessionList.BINDINGS}
            hidden = [binding.description for key, binding in app._bindings if key in declared and not binding.show and binding.description]

            assert hidden == ["Clear search"], f"an action nobody can see: {hidden}"

    async def test_it_fits_a_full_screen_terminal(self, world) -> None:
        """118 columns is what a maximised window has on the reference machine."""
        app = SessionList()
        async with app.run_test(size=(118, 20)) as pilot:
            await ready(app, pilot)
            footer = app.query_one(Footer)
            wanted = sum(key.size.width for key in footer.query("FooterKey"))

            assert wanted <= footer.size.width, f"the footer wants {wanted} of {footer.size.width}"

    async def test_the_palette_is_off_rather_than_half_empty(self, world) -> None:
        """Textual enables one by default. Left on with no provider of ours, it would offer only the framework's theme and screenshot commands."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)

            assert app.ENABLE_COMMAND_PALETTE is False
            assert "palette" not in drawn(app)


class TestTheListWillNotReopenARunningSession:
    async def test_it_refuses_when_it_cannot_tell_what_is_running(self, world, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The guard used to live in `default_selection`, which only the command line called; the list wrote its own filter and opened everything."""
        root, set_live = world
        running = [live_session("hist1", root), live_session("hist2", root)]
        set_live(*running)
        workspaces.save(workspaces.from_live("w", running))
        launched: list[object] = []
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": launched.append(groups))

        app = SessionList()
        async with app.run_test(notifications=True) as pilot:
            await ready(app, pilot)
            set_live(unavailable="`claude` was not found on PATH.")
            await app._restore_workspace("w")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert launched == [], "both sessions are running; opening them would make a second copy of each"
            assert any("PATH" in notification.message for notification in app._notifications), "and it says why"


class TestASavedSetCanBeMaintainedFromTheList:
    """Refresh, add and remove are required affordances rather than optional ones, because a fixed list ossifies without them. Add, remove and rename existed only as subcommands, so half of that was true on one surface."""

    async def test_the_picker_offers_every_way_to_maintain_a_set(self, world) -> None:
        offered = {binding.description for binding in WorkspacePicker.BINDINGS}

        assert {"Restore", "Refresh from live", "Edit members", "Rename", "Delete"} <= offered

    async def test_dropping_a_member_goes_through_the_service(self, world, home: Path) -> None:
        root, _ = world
        workspaces.save(workspaces.from_live("w", [live_session("hist1", root), live_session("hist2", root)]))

        app = SessionList()
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await ready(app, pilot)
            # On a worker because the screens use `push_screen_wait`; in the app it is `action_workspaces` that supplies one.
            app.run_worker(app._edit_members("w"))
            await screen_of(app, pilot, WorkspaceMembers)
            await pilot.press("x")
            await screen_of(app, pilot, WorkspaceMembers)  # the loop reopens it, so a second drop needs no reopening
            await pilot.press("escape")
            await pilot.pause()

        assert len(workspaces.load("w").members) == 1, "dropping a member is an edit, not a delete of the whole set"

    async def test_only_running_sessions_can_be_added(self, world, home: Path) -> None:
        """`service.add_sessions` accepts nothing else: a member carries the working directory and name that `claude agents --json` reported."""
        root, set_live = world
        workspaces.save(workspaces.from_live("w", [live_session("hist2", root)]))
        set_live(live_session("hist1", root))

        app = SessionList()
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await ready(app, pilot)
            app.run_worker(app._edit_members("w"))
            await screen_of(app, pilot, WorkspaceMembers)
            await pilot.press("a")
            adding = await screen_of(app, pilot, AddSessions)

            assert [row.session_id for row in adding.candidates] == ["hist1"], "hist2 is already in the set and nothing else is running"

            await pilot.press("space")
            await pilot.press("a")
            await screen_of(app, pilot, WorkspaceMembers)
            await pilot.press("escape")
            await pilot.pause()

        assert sorted(workspaces.load("w").session_ids) == ["hist1", "hist2"]

    async def test_renaming_goes_through_the_service(self, world) -> None:
        root, _ = world
        workspaces.save(workspaces.from_live("old", [live_session("hist1", root)]))

        app = SessionList()
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await ready(app, pilot)
            app.run_worker(app._rename_workspace("old"))
            screen = await screen_of(app, pilot, RenameWorkspace)
            screen.query_one(Input).value = "new"
            await pilot.press("enter")
            await pilot.pause()

        assert workspaces.exists("new")
        assert not workspaces.exists("old"), "the sessions and the creation date come with it"

    async def test_accepting_the_unchanged_name_renames_nothing(self, world, monkeypatch: pytest.MonkeyPatch) -> None:
        """The rename box opens prefilled, so enter on it is the commonest accident. The dialog answers with the name and the list drops it rather than renaming a workspace onto itself."""
        root, _ = world
        workspaces.save(workspaces.from_live("old", [live_session("hist1", root)]))
        renames: list[tuple[str, str]] = []
        monkeypatch.setattr(service, "rename_workspace", lambda old, new: renames.append((old, new)) or service.Outcome(ok=True, message="ok"))

        app = SessionList()
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await ready(app, pilot)
            app.run_worker(app._rename_workspace("old"))
            await screen_of(app, pilot, RenameWorkspace)
            await pilot.press("enter")
            await pilot.pause()

        assert renames == [], "nothing may be renamed onto itself"
        assert workspaces.exists("old")


class TestADeadMemberIsSaidOutLoud:
    """Dead ids are surfaced on restore and droppable from the member list."""

    async def test_restoring_names_them_and_says_where_to_drop_them(self, world, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, _ = world
        # Every test that reaches `_restore_workspace` must stub the launcher: without it this really does spawn Windows Terminal panes running `claude --resume`.
        monkeypatch.setattr(launcher.WindowsTerminalLauncher, "launch", lambda self, groups, *, fork=False, window="new": None)
        monkeypatch.setattr(service, "SETTLE_SECONDS", 0)
        workspaces.save(workspaces.from_live("w", [live_session("hist2", root), live_session("ghost", root)]))

        app = SessionList()
        async with app.run_test(notifications=True) as pilot:
            await ready(app, pilot)
            await app._restore_workspace("w")
            await app.workers.wait_for_complete()
            await pilot.pause()

            said = [notification.message for notification in app._notifications]
            assert any("no longer have a transcript" in message for message in said), said
            assert any("Press e" in message for message in said), "and the way to drop them is on the same surface"

    async def test_the_member_list_marks_them(self, world, home: Path) -> None:
        root, _ = world
        workspaces.save(workspaces.from_live("w", [live_session("hist2", root), live_session("ghost", root)]))

        app = SessionList()
        async with app.run_test(size=(100, 30)) as pilot:
            await ready(app, pilot)
            app.run_worker(app._edit_members("w"))
            await screen_of(app, pilot, WorkspaceMembers)
            await pilot.pause()

            assert "transcript gone" in drawn(app)
            await pilot.press("escape")


class TestKeeping:
    async def test_k_sets_a_session_aside_without_asking_anything(self, world) -> None:
        """One keypress is the requirement rather than a convenience: a capture step that demands typing does not get used."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[0]
            await pilot.press("k")
            await pilot.pause()

            assert not isinstance(app.screen, Dialog)
            assert target.kept is True
            assert keep.kept() == {target.session_id}

    async def test_pressing_k_again_releases_it(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[0]
            await pilot.press("k")
            await pilot.pause()
            app._move_cursor_to(target.session_id)
            await pilot.press("k")
            await pilot.pause()

            assert target.kept is False
            assert keep.kept() == set()

    async def test_a_kept_row_is_marked_bold(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[0]
            target.kept = True

            assert "bold" in _row_style(target, app._palette())

    async def test_an_archived_row_is_still_dim(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[0]
            target.archived = True

            assert "dim" in _row_style(target, app._palette())

    async def test_keeping_an_archived_row_brings_it_back_to_the_list(self, world) -> None:
        """The row has to agree with the store, or the list shows a state that is not on disk."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[0]
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("h")  # show the archived ones so the row can be selected again
            await pilot.pause()
            app._move_cursor_to(target.session_id)
            await pilot.press("k")
            await pilot.pause()

            assert target.kept is True
            assert target.archived is False

    async def test_a_kept_row_climbs_to_the_top_of_the_list(self, world) -> None:
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            target = app.filtered[-1]
            app._move_cursor_to(target.session_id)
            await pilot.press("k")
            await pilot.pause()

            assert app.filtered[0].session_id == target.session_id

    async def test_the_footer_still_fits_with_the_keep_key(self, world) -> None:
        app = SessionList()
        async with app.run_test(size=(118, 30)) as pilot:
            await ready(app, pilot)
            footer = app.query_one(Footer)

            assert footer.size.width >= len(str(footer.render()))


class TestAddingToAWorkspace:
    async def test_the_add_screen_offers_sessions_that_are_not_running(self, world) -> None:
        """`hist1` and `hist2` are finished conversations; before this they were filtered out of the candidate list."""
        app = SessionList()
        async with app.run_test() as pilot:
            await ready(app, pilot)
            workspaces.save(workspaces.from_members("w", []))
            await pilot.press("w")
            picker = await screen_of(app, pilot, WorkspacePicker)
            picker.dismiss(("members", "w"))
            members = await screen_of(app, pilot, WorkspaceMembers)
            members.dismiss(("add", []))
            adding = await screen_of(app, pilot, AddSessions)

            assert {row.session_id for row in adding.candidates} == {"hist1", "hist2"}
