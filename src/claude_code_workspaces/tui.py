"""The session list `ccw` opens with no arguments.

One table for live and historical rows; the status glyph carries the distinction.
"""

import asyncio
import re
from dataclasses import dataclass

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Input, Static
from textual.widgets.data_table import ColumnKey

from . import live as live_source
from . import restore, service, sessions, shutdown, snapshot, transcripts, workspaces
from .checklist import RestoreChecklist
from .dialogs import AddSessions, Confirm, NameWorkspace, RenameWorkspace, WorkspaceMembers, WorkspacePicker
from .formatting import age, clip
from .live import LiveSession
from .restore import RestorePlan
from .service import Outcome
from .sessions import SessionRow

# Do not hammer `claude agents --json`, but notice a session flipping to `waiting`.
POLL_SECONDS = 5.0

GLYPH_WIDTH = 1
AGE_WIDTH = 9
MIN_NAME_WIDTH = 24
# Percentile rather than longest: one outlier would otherwise widen every row.
NAME_PERCENTILE = 90
MAX_PROJECT_WIDTH = 24
MAX_BRANCH_WIDTH = 8
NAME_HEADING = "Session"
PROJECT_HEADING = "Project"
BRANCH_HEADING = "Branch"
_CELL_PADDING = 2  # DataTable pads each cell on both sides.
_COLUMN_COUNT = 5


def _percentile(values: list[int], percentile: int) -> int:
    """Avoid sizing to the longest name — one outlier widens every row."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, len(ordered) * percentile // 100)]


@dataclass(slots=True, frozen=True)
class Widths:
    name: int
    project: int
    branch: int


def _row_style(row: SessionRow, palette: dict[str, str]) -> str:
    """Terminal attributes rather than colour, so both marks survive monochrome and neither collides with the palette. They never combine: keep and archive are exclusive."""
    if row.archived:
        return "dim"
    if row.kept:
        return f"bold {palette.get(row.status or '', '')}".strip()
    return palette.get(row.status or "", "")


class SessionList(App[None]):
    """Named for the list it shows — not "workspaces", which collides with the dialog that lists those."""

    TITLE = "Claude Code Workspaces"

    # Eleven footer keys leave a palette nothing useful to carry.
    ENABLE_COMMAND_PALETTE = False

    # Default `"*"` focuses the first focusable widget; `focusable` tests `visible` not `display`, so a hidden search box stole focus on first load.
    AUTO_FOCUS = "DataTable"

    CSS = """
    Vertical { height: 1fr; }
    #search { dock: top; display: none; }
    #search.-active { display: block; }
    DataTable { height: 1fr; }
    /* A row of the column, not docked. Docking it to the bottom of the screen put it in the same region as the Footer, which is docked there by Textual and mounted after it, so the status line was drawn and then covered for as long as it has existed. */
    #status { height: 1; padding: 0 1; color: $text-muted; }
    /* Textual keys the footer in $warning, which is the colour a session waiting on you takes. Ten permanent hints and one urgent row were sharing a hue, and the hints won by being always there. The keys keep their weight from being bold; colour is left to mean "this needs you". */
    FooterKey .footer-key--key { color: $foreground; }
    """

    # `enter` is not listed: DataTable consumes it; selection is an event (also enables mouse click).
    # Short labels: a fixed footer cannot hold the long ones.
    BINDINGS = [
        Binding("o", "resume", "Resume"),
        Binding("f", "fork", "Fork"),
        Binding("c", "copy", "Copy"),
        Binding("a", "archive", "Archive"),
        Binding("k", "keep", "Keep"),
        Binding("s", "save_workspace", "Save"),
        Binding("w", "workspaces", "Workspaces"),
        Binding("p", "restore_previous", "Previous"),
        Binding("slash", "search", "Search"),
        # Not `A`: archive and un-hide are opposite; one shift apart made typing `a` archive when meaning show.
        Binding("h", "toggle_archived", "Hidden"),
        Binding("r", "reload", "Reload"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "clear_search", "Clear search", show=False),
    ]

    searching = reactive(False)

    def watch_searching(self, searching: bool) -> None:
        self.query_one("#search", Input).set_class(searching, "-active")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[SessionRow] = []
        self.filtered: list[SessionRow] = []
        self.show_archived = False
        self.note: str | None = None
        self._loading = True
        self._awaiting_live = True
        self._statuses: dict[str, str] = {}
        self._poll_timer: Timer | None = None
        self._window_focused = True
        self._name_column: ColumnKey | None = None
        self._project_column: ColumnKey | None = None
        self._branch_column: ColumnKey | None = None

    def compose(self) -> ComposeResult:
        # No Header: costs a row; TITLE still sets the terminal title.
        with Vertical():
            yield Input(placeholder="Search name, path, branch, first message — or paste a session id", id="search")
            yield DataTable(cursor_type="row", zebra_stripes=True)
            yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column(" ", width=GLYPH_WIDTH, key="glyph")
        self._name_column = table.add_column(NAME_HEADING, width=MIN_NAME_WIDTH, key="name")
        self._project_column = table.add_column(PROJECT_HEADING, width=MAX_PROJECT_WIDTH, key="project")
        self._branch_column = table.add_column(BRANCH_HEADING, width=MAX_BRANCH_WIDTH, key="branch")
        table.add_column("Last seen", width=AGE_WIDTH, key="age")
        self.action_reload()
        self._poll_timer = self.set_interval(POLL_SECONDS, self._poll)

    # --- data -------------------------------------------------------------

    def _reload_worker(self) -> None:
        """Paint history first, then fold in live — waiting for both froze startup; this also feeds the snapshot so `claude` runs once."""
        self._deliver(self._apply_history, sessions.history_rows())
        live, note = live_source.try_live_sessions()
        self._deliver(self._apply_live, live, note)
        sessions.prune_archive(self.rows)
        # Not via `service`: silent write; `snapshot.take` returns None rather than raising.
        snapshot.take(live or [])

    def _deliver(self, method, *args) -> None:
        """Skip `call_from_thread` after quit — an abandoned worker would otherwise traceback on the way out."""
        if self.is_running:
            self.call_from_thread(method, *args)

    def _apply_history(self, rows: list[SessionRow]) -> None:
        self.rows = rows
        self._loading = False
        self._repaint()

    def _widths(self) -> Widths:
        """Sized over every row, not the filter, so columns do not jump while typing a search."""
        table = self.query_one(DataTable)
        # scrollable_content_region: a scrollbar's columns otherwise clip "Last seen".
        available = table.scrollable_content_region.width or table.size.width

        def fitted(heading: str, longest: int, cap: int) -> int:
            # Never narrower than the heading (or "Project" becomes "Pro").
            return max(len(heading), min(longest, cap))

        branch = fitted(BRANCH_HEADING, max((len(row.branch or "") for row in self.rows), default=0), MAX_BRANCH_WIDTH)
        project = fitted(PROJECT_HEADING, max((len(row.root.name) for row in self.rows), default=0), MAX_PROJECT_WIDTH)
        spare = available - GLYPH_WIDTH - branch - AGE_WIDTH - _CELL_PADDING * _COLUMN_COUNT
        project = max(len(PROJECT_HEADING), min(project, spare - MIN_NAME_WIDTH))
        wanted = _percentile([len(row.label) for row in self.rows], NAME_PERCENTILE)
        name = max(MIN_NAME_WIDTH, min(wanted, spare - project))
        return Widths(name=name, project=project, branch=branch)

    def on_resize(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        query = self.query_one("#search", Input).value
        candidates = self.rows if self.show_archived else [row for row in self.rows if not row.archived]
        self.filtered = sessions.search(candidates, query)

        table = self.query_one(DataTable)
        widths = self._widths()
        for key, width in ((self._name_column, widths.name), (self._project_column, widths.project), (self._branch_column, widths.branch)):
            if key is not None:
                table.columns[key].width = width
        highlighted = self._highlighted_id()
        # Preserve scroll: clear()+rebuild would yank the view back to the cursor on every poll.
        looking_at = table.scroll_offset
        table.clear()
        palette = self._palette()
        for row in self.filtered:
            cells = (row.glyph, clip(row.label, widths.name), clip(row.root.name, widths.project), clip(row.branch or "", widths.branch), age(row.last_active))
            table.add_row(*(Text(cell, style=_row_style(row, palette)) for cell in cells), key=row.session_id)
        if highlighted is not None:
            self._move_cursor_to(highlighted, scroll=False)
        table.scroll_to(x=looking_at.x, y=looking_at.y, animate=False)
        self._describe()

    def _palette(self) -> dict[str, str]:
        """Theme colours for live statuses. Include idle so open sessions are not the same shade as finished ones; skip text-accent — it collides with text-warning here."""
        return {
            "waiting": self.theme_variables.get("text-warning", "yellow"),
            "busy": self.theme_variables.get("text-success", "green"),
            "idle": self.theme_variables.get("text-primary", "blue"),
        }

    def _describe(self) -> None:
        if self._loading:
            self.query_one("#status", Static).update("Reading transcripts...")
            return
        live = sum(1 for row in self.filtered if row.live)
        waiting = sum(1 for row in self.filtered if row.status == "waiting")
        parts = [f"{len(self.filtered)} of {len(self.rows)} sessions"]
        # Avoid "0 live" while the live read is still in flight.
        parts.append("checking what is running..." if self._awaiting_live else f"{live} live")
        if not self._awaiting_live:
            parts.append(f"{waiting} waiting")
        archived = sum(1 for row in self.rows if row.archived)
        if archived:
            parts.append(f"{archived} archived (shown)" if self.show_archived else f"{archived} archived")
        if self.note:
            parts.append(self.note)
        # Surface unreadable transcripts so degrade is visible.
        unreadable = len(transcripts.errors())
        if unreadable:
            parts.append(f"{unreadable} transcript(s) unreadable")
        self.query_one("#status", Static).update("   ".join(parts))

    def on_app_blur(self) -> None:
        """Pause polls while unfocused — saves battery; terminals that never report focus keep polling."""
        self._window_focused = False
        if self._poll_timer is not None:
            self._poll_timer.pause()

    def on_app_focus(self) -> None:
        """Poll immediately so the list is not stale when focus returns."""
        self._window_focused = True
        if self._poll_timer is not None:
            self._poll_timer.resume()
        self._poll()

    def _poll(self) -> None:
        """Threaded — a sync read here froze the list every interval."""
        # `_awaiting_live`, not `_loading`: the reload worker clears `_loading` when it paints history and only then does its own live read, so `_loading` left a window where a tick spawned a second `claude agents --json`. This covers the whole reload, so the older gate was redundant as well as leaky.
        if self._awaiting_live or not self._window_focused:
            return
        self.run_worker(self._poll_worker, thread=True, name="poll", group="poll", exclusive=True)

    def _poll_worker(self) -> None:
        live, note = live_source.try_live_sessions()
        self._deliver(self._apply_live, live, note)
        sessions.prune_archive(self.rows)

    def _apply_live(self, live: list[LiveSession] | None, note: str | None) -> None:
        """Must run on the message loop — the only place rows and the table are safe to touch."""
        previous = self._statuses
        self.note = note
        self._awaiting_live = False
        self.rows = sessions.merge_live(self.rows, live)
        self._statuses = sessions.statuses(self.rows)
        for row in sessions.waiting_now(previous, self.rows):
            self.notify(f"{row.label} is waiting for you.", title=row.root.name, severity="warning", timeout=10)
        self._repaint()

    # --- selection --------------------------------------------------------

    def _highlighted_id(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.is_valid_row_index(table.cursor_row):
            return None
        return str(table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value)

    def _selected(self) -> SessionRow | None:
        session_id = self._highlighted_id()
        return next((row for row in self.filtered if row.session_id == session_id), None)

    def _move_cursor_to(self, session_id: str, *, scroll: bool = True) -> bool:
        """`scroll=False` for repaint restore — must not drag the view with the cursor."""
        table = self.query_one(DataTable)
        for index, row in enumerate(self.filtered):
            if row.session_id == session_id:
                table.move_cursor(row=index, scroll=scroll)
                return True
        return False

    # --- actions ----------------------------------------------------------

    def action_reload(self) -> None:
        """Threaded: a cold rebuild costs seconds and would freeze the UI."""
        self._loading = True
        self._awaiting_live = True
        self._describe()
        self.run_worker(self._reload_worker, thread=True, name="reload", group="reload", exclusive=True)

    async def action_quit(self) -> None:
        """Abandon in-flight live reads so quit is not blocked for seconds."""
        shutdown.begin()
        live_source.abandon_in_flight()
        self.exit()

    def action_search(self) -> None:
        """Focus the box so the next keystroke searches, not a binding."""
        self.searching = True
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        self.query_one(Input).value = ""
        self.searching = False
        self.query_one(DataTable).focus()
        self._repaint()

    def action_copy(self) -> None:
        row = self._selected()
        if row is None:
            return
        self.copy_to_clipboard(row.resume_command)
        self.notify(row.resume_command, title="Copied")

    def action_resume(self) -> None:
        self._open(fork=False)

    def action_fork(self) -> None:
        self._open(fork=True)

    def _open(self, *, fork: bool) -> None:
        row = self._selected()
        if row is not None:
            self._report(service.resume_session(row, fork=fork))

    def action_archive(self) -> None:
        """No confirmation: archive only records an id and moves nothing."""
        row = self._selected()
        if row is None:
            return
        outcome = service.archive_session(row.session_id, row.label)
        if self._report(outcome):
            row.archived = not row.archived
            # `service.archive_session` released the keep mark on disk; the row has to say the same or the list shows a state that is not there.
            row.kept = row.kept and not row.archived
            self._repaint()

    def action_keep(self) -> None:
        """No confirmation and no prompt: setting something aside has to cost one keypress, or it does not get used."""
        row = self._selected()
        if row is None:
            return
        if self._report(service.keep_session(row.session_id, row.label)):
            row.kept = not row.kept
            row.archived = row.archived and not row.kept
            # Keeping changes where the row belongs, and `_repaint` only filters. Without this the shelf does not move until the next reload.
            self.rows = sessions.ordered(self.rows)
            self._repaint()

    def action_toggle_archived(self) -> None:
        self.show_archived = not self.show_archived
        self._repaint()

    # --- workspaces -------------------------------------------------------
    # All workspace actions go through `service` so TUI and CLI cannot disagree.

    def _report(self, outcome: Outcome, *, title: str | None = None) -> bool:
        """TUI half of the `cli._report` boundary."""
        self.notify(outcome.message, title=title or "", severity="information" if outcome.ok else "error")
        return outcome.ok

    @work
    async def action_save_workspace(self) -> None:
        live = [row for row in self.rows if row.live]
        if not live:
            self.notify("Nothing is running, so there is nothing to save.", severity="warning")
            return
        name = await self.push_screen_wait(NameWorkspace(len(live), _suggested_name(live)))
        if name is None:
            return
        self._report(service.save_workspace(name))

    @work
    async def action_restore_previous(self) -> None:
        """Always shows the checklist first — the plan's mtime heuristic is fuzzy at the edges."""
        plan = await asyncio.to_thread(restore.build_plan)
        if not plan.entries:
            self.notify(restore.EMPTY_CRASH_PLAN, severity="warning")
            return
        chosen = await self.push_screen_wait(RestoreChecklist(plan, title="Restore — what was running before"))
        if not chosen:
            return
        self._launch(plan, chosen)

    @work
    async def action_workspaces(self) -> None:
        """Workspace actions live one screen in — the session-list footer has no spare column."""
        chosen = await self.push_screen_wait(WorkspacePicker(workspaces.load_all()))
        if chosen is None:
            return
        action, name = chosen
        if action == "restore":
            await self._restore_workspace(name)
        elif action == "refresh":
            self._report(service.refresh_workspace(name))
        elif action == "members":
            await self._edit_members(name)
        elif action == "rename":
            await self._rename_workspace(name)
        elif action == "delete":
            await self._delete_workspace(name)

    async def _restore_workspace(self, name: str) -> None:
        resolved = await asyncio.to_thread(service.plan_named_restore, name)
        if resolved.dead:
            self.notify(f"{resolved.dead_note} Press e on the workspace list to drop them.", severity="warning")
        if resolved.blocked is not None or resolved.plan is None:
            self._report(resolved.blocked or Outcome(ok=False, message=f"No workspace called '{name}'."))
            return
        if resolved.over_cap:
            agreed = await self.push_screen_wait(Confirm(resolved.question, resolved.detail, confirm=f"Open {len(resolved.openable)}"))
            if not agreed:
                return
        self._launch(resolved.plan, list(resolved.openable), source=name)

    async def _rename_workspace(self, name: str) -> None:
        new = await self.push_screen_wait(RenameWorkspace(name))
        if new is not None and new != name:
            self._report(service.rename_workspace(name, new))

    async def _edit_members(self, name: str) -> None:
        """Loop until closed and re-read each pass — multi-edit without reopening screens."""
        while True:
            view = await asyncio.to_thread(service.view_members, name)
            if view.blocked is not None:
                self._report(view.blocked)
                return
            chosen = await self.push_screen_wait(WorkspaceMembers(name, list(view.entries)))
            if chosen is None:
                return
            action, session_ids = chosen
            if action == "drop":
                self._report(service.remove_sessions(name, session_ids))
            elif action == "add":
                await self._add_members(name, list(view.session_ids))

    async def _add_members(self, name: str, present: list[str]) -> None:
        """Every session the list knows, not just the running ones: a finished conversation is a perfectly good member."""
        already = set(present)
        candidates = [row for row in self.rows if row.session_id not in already]
        chosen = await self.push_screen_wait(AddSessions(name, candidates))
        if chosen is not None:
            self._report(service.add_sessions(name, chosen, self.rows))

    def _launch(self, plan: RestorePlan, session_ids: list[str], *, source: str = "") -> None:
        """Both restore paths land here so cap and snapshot apply once."""
        if self._report(service.launch_restore(plan, session_ids, source=source)):
            # Off the message loop — recording the restored set can wait.
            self.run_worker(service.record_restored_set, thread=True, name="snapshot")

    async def _delete_workspace(self, name: str) -> None:
        described = service.describe_workspace(name)
        if not described.ok:
            self._report(described)
            return
        agreed = await self.push_screen_wait(
            Confirm(f"Move '{name}' to the trash?", f"{described.message} The conversations themselves are untouched."),
        )
        if agreed:
            self._report(service.delete_workspace(name))

    @on(DataTable.RowSelected)
    def _on_row_selected(self) -> None:
        self.action_resume()

    @on(Input.Changed, "#search")
    def _on_search(self) -> None:
        self._repaint()

    @on(Input.Submitted, "#search")
    def _on_submit(self) -> None:
        self.query_one(DataTable).focus()


def _suggested_name(live: list[SessionRow]) -> str:
    """Empty unless every live session shares one project — a wrong suggestion is worse than none."""
    roots = {row.root.name for row in live}
    if len(roots) != 1:
        return ""
    candidate = re.sub(r"[^a-z0-9._-]+", "-", roots.pop().lower()).strip("-.")
    # Checked before `exists`, which validates and raises: a directory called `_foo` or one longer than the name limit would otherwise take the save worker down instead of leaving the box empty.
    if not workspaces.NAME_PATTERN.match(candidate):
        return ""
    return candidate if not workspaces.exists(candidate) else ""


def run() -> None:
    SessionList().run()
