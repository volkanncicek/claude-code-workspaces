"""Modal dialogs the session list opens.

Shared layout via `Dialog`; workspace screens read through `workspaces` so they stay aligned with the CLI.
"""

from typing import ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, SelectionList
from textual.widgets.button import ButtonVariant
from textual.widgets.selection_list import Selection

from . import workspaces
from .formatting import age
from .restore import RestoreEntry
from .sessions import SessionRow
from .workspaces import Workspace, WorkspaceError

# Cap table height so a long list cannot push the buttons off screen.
MAX_TABLE_ROWS = 14

# What a picker asks the session list to do next. Named so the dispatch in `tui` cannot drift from what a dialog can dismiss with.
WorkspaceAction = Literal["restore", "refresh", "members", "rename", "delete"]
MemberAction = Literal["add", "drop"]


class DialogBox(VerticalGroup):
    """`VerticalGroup` rather than `Vertical`: the latter defaults to `height: 1fr` and stretched every dialog."""

    DEFAULT_CSS = """
    DialogBox {
        width: 68;
        max-height: 80%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    DialogBox > Label { width: 100%; height: auto; }
    DialogBox > #hint { color: $text-muted; padding-top: 1; }
    /* Reserved whether or not there is an error, so the box does not jump when one appears. */
    DialogBox > #error { color: $error; height: 1; }
    DialogBox > Input { width: 100%; margin: 1 0 0 0; }
    """


class ButtonRow(HorizontalGroup):
    """Right-aligned answers. `HorizontalGroup` for the same reason as `DialogBox`."""

    DEFAULT_CSS = """
    ButtonRow {
        width: 100%;
        align-horizontal: right;
        padding-top: 1;
    }
    ButtonRow > Button { margin-left: 2; min-width: 12; }
    """


class Dialog[T](ModalScreen[T]):
    """Centred box; each screen sets AUTO_FOCUS — the app's selector names a widget no dialog contains."""

    AUTO_FOCUS = "#cancel"

    DEFAULT_CSS = """
    Dialog { align: center middle; }
    """

    def buttons(self, *, confirm: str, variant: ButtonVariant = "primary", cancel: str = "Cancel") -> ComposeResult:
        """Safe answer first so focus and the eye land there."""
        with ButtonRow(id="buttons"):
            yield Button(cancel, id="cancel")
            yield Button(confirm, variant=variant, id="confirm")


class AskName(Dialog[str | None]):
    """Validate via `workspaces` before write. Exists-check is not relaxed for rename — colliding names would silently lose one.

    Abstract: a subclass supplies the two strings. Empty defaults would render an untitled box with a blank button rather than fail, so there are none.
    """

    AUTO_FOCUS = "#name"

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    box_title: ClassVar[str]
    confirm_label: ClassVar[str]

    def __init__(self, body: str, value: str = "", exempt: str = "") -> None:
        super().__init__()
        self.body = body
        self.value = value
        # The one name the exists-check must let through: rename opens prefilled with it, so refusing it would be an error for pressing enter on an unchanged box.
        self.exempt = exempt

    def compose(self) -> ComposeResult:
        with DialogBox(id="box") as box:
            box.border_title = self.box_title
            yield Label(self.body)
            yield Input(value=self.value, placeholder="a name for this set of conversations", id="name")
            yield Label("Lower-case letters, digits, dot, dash or underscore.", id="hint")
            yield Label("", id="error")
            yield from self.buttons(confirm=self.confirm_label)
        yield Footer()

    def on_input_submitted(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        # These checks are for inline feedback — a modal has to refuse without dismissing, which a `service` call cannot do. `service.save_workspace` makes both again and is the authority; if they ever disagree, this one is the wrong one.
        name = self.query_one(Input).value.strip()
        try:
            workspaces.validate(name)
        except WorkspaceError as exc:
            self._complain(str(exc))
            return
        if name != self.exempt and workspaces.exists(name):
            self._complain(f"'{name}' already exists. Pick another name, or refresh it from the workspace list.")
            return
        self.dismiss(name)

    def _complain(self, message: str) -> None:
        self.query_one("#error", Label).update(message)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NameWorkspace(AskName):
    box_title = "Save workspace"
    confirm_label = "Save"

    def __init__(self, live_count: int, suggestion: str = "") -> None:
        super().__init__(f"These {live_count} running sessions will be saved together under one name, and stay that set until you change it.", suggestion)


class RenameWorkspace(AskName):
    box_title = "Rename workspace"
    confirm_label = "Rename"

    def __init__(self, current: str) -> None:
        super().__init__(f"'{current}' keeps its sessions and the date it was created. Only the name changes.", current, exempt=current)


class WorkspacePicker(Dialog[tuple[WorkspaceAction, str] | None]):
    """Dismisses with `(action, name)`; this screen only reads."""

    BINDINGS = [
        Binding("o", "open", "Restore"),
        Binding("f", "refresh", "Refresh from live"),
        Binding("e", "members", "Edit members"),
        Binding("r", "rename", "Rename"),
        Binding("d", "delete", "Delete"),
        Binding("escape,q", "cancel", "Close"),
    ]

    AUTO_FOCUS = "DataTable"

    CSS = """
    WorkspacePicker DialogBox { width: 76; }
    WorkspacePicker #empty { color: $text-muted; padding: 1 0; }
    """

    def __init__(self, found: list[Workspace]) -> None:
        super().__init__()
        self.found = found

    def compose(self) -> ComposeResult:
        with DialogBox(id="box") as box:
            box.border_title = "Workspaces"
            if self.found:
                yield DataTable(cursor_type="row")
                yield Label("Restore opens every session in a workspace. Refresh replaces its contents with what is running now; edit changes it one session at a time.", id="hint")
            else:
                yield Label("No workspaces yet. Press s on the session list to save the running set under a name.", id="empty")
                with ButtonRow(id="buttons"):
                    yield Button("Close", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        if not self.found:
            return
        table = self.query_one(DataTable)
        # `height: auto` collapses a DataTable; size to the rows it has.
        table.styles.height = min(len(self.found) + 1, MAX_TABLE_ROWS)
        table.add_columns("Workspace", "Sessions", "Updated")
        for workspace in self.found:
            table.add_row(workspace.name[:34], str(len(workspace.members)), age(workspace.updated), key=workspace.name)

    def _choose(self, action: WorkspaceAction) -> None:
        if not self.found:
            return
        table = self.query_one(DataTable)
        if table.is_valid_row_index(table.cursor_row):
            self.dismiss((action, self.found[table.cursor_row].name))

    def action_open(self) -> None:
        self._choose("restore")

    def action_refresh(self) -> None:
        self._choose("refresh")

    def action_members(self) -> None:
        self._choose("members")

    def action_rename(self) -> None:
        self._choose("rename")

    def action_delete(self) -> None:
        self._choose("delete")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self) -> None:
        self.dismiss(None)

    def on_data_table_row_selected(self) -> None:
        self._choose("restore")


class WorkspaceMembers(Dialog[tuple[MemberAction, list[str]] | None]):
    """Dismisses with `(action, session_ids)`. Dead members stay visible (dim) — dropping them is the user's call."""

    BINDINGS = [
        Binding("a", "add", "Add running"),
        Binding("x", "drop", "Drop"),
        Binding("escape,q", "cancel", "Close"),
    ]

    AUTO_FOCUS = "DataTable"

    CSS = """
    WorkspaceMembers DialogBox { width: 84; }
    WorkspaceMembers #empty { color: $text-muted; padding: 1 0; }
    """

    def __init__(self, workspace_name: str, entries: list[RestoreEntry]) -> None:
        super().__init__()
        # Not `self.name`: Textual's DOMNode already has a read-only `name`. Spelled out rather than `workspace`, which reads as a `Workspace`.
        self.workspace_name = workspace_name
        self.entries = entries

    def compose(self) -> ComposeResult:
        with DialogBox(id="box") as box:
            box.border_title = f"Members of '{self.workspace_name}'"
            if self.entries:
                yield DataTable(cursor_type="row")
                yield Label("Dropping a session leaves the conversation itself untouched; it only stops being part of this set.", id="hint")
            else:
                yield Label(f"'{self.workspace_name}' holds no sessions. Press a to add one that is running.", id="empty")
                with ButtonRow(id="buttons"):
                    yield Button("Close", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        if not self.entries:
            return
        table = self.query_one(DataTable)
        table.styles.height = min(len(self.entries) + 1, MAX_TABLE_ROWS)
        table.add_columns("Session", "Project", "Last seen", "State")
        for entry in self.entries:
            cells = (entry.label[:34], entry.root.name[:20], age(entry.last_active), _member_state(entry))
            # Dim rather than colour: marked on monochrome and never collides with the session-list palette.
            style = "dim" if entry.missing else ""
            table.add_row(*(Text(cell, style=style) for cell in cells), key=entry.session_id)

    def _dismiss_with(self, action: MemberAction, session_ids: list[str]) -> None:
        self.dismiss((action, session_ids))

    def action_add(self) -> None:
        self._dismiss_with("add", [])

    def action_drop(self) -> None:
        if not self.entries:
            return
        table = self.query_one(DataTable)
        if table.is_valid_row_index(table.cursor_row):
            self._dismiss_with("drop", [self.entries[table.cursor_row].session_id])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self) -> None:
        self.dismiss(None)


def _member_state(entry: RestoreEntry) -> str:
    if entry.missing:
        return "transcript gone"
    return "running" if entry.live else ""


class AddSessions(Dialog[list[str] | None]):
    """Any session the list knows, running or not."""

    BINDINGS = [
        Binding("a", "confirm", "Add checked"),
        Binding("space", "toggle_current", "Check / uncheck"),
        Binding("escape,q", "cancel", "Cancel"),
    ]

    AUTO_FOCUS = "SelectionList"

    CSS = """
    AddSessions DialogBox { width: 84; }
    AddSessions SelectionList { height: auto; max-height: 14; }
    AddSessions #empty { color: $text-muted; padding: 1 0; }
    """

    def __init__(self, workspace_name: str, candidates: list[SessionRow]) -> None:
        super().__init__()
        self.workspace_name = workspace_name
        self.candidates = candidates

    def compose(self) -> ComposeResult:
        with DialogBox(id="box") as box:
            box.border_title = f"Add to '{self.workspace_name}'"
            if self.candidates:
                yield SelectionList[str](*[Selection(f"{row.label:<40.40}  {row.root.name:<20.20}  {row.status or ''}", row.session_id) for row in self.candidates])
                yield Label("Nothing is checked to begin with: adding to a saved set is a deliberate edit, not a default.", id="hint")
            else:
                yield Label("Every session is already in this set.", id="empty")
                with ButtonRow(id="buttons"):
                    yield Button("Close", id="cancel")
        yield Footer()

    def action_toggle_current(self) -> None:
        if self.candidates:
            self.query_one(SelectionList).action_select()

    def action_confirm(self) -> None:
        if not self.candidates:
            return
        chosen = list(self.query_one(SelectionList).selected)
        if not chosen:
            self.notify("Nothing is checked.", severity="warning")
            return
        self.dismiss(chosen)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self) -> None:
        self.dismiss(None)


class Confirm(Dialog[bool]):
    """Anything that moves a file asks first. The button says what will happen, not "Yes"."""

    # Arrow keys for two side-by-side buttons; Tab already moves between them.
    BINDINGS = [
        Binding("left,up", "previous", "", show=False),
        Binding("right,down", "next", "Change answer"),
        Binding("escape", "no", "Cancel"),
    ]

    CSS = """
    Confirm DialogBox { border: round $error; }
    """

    def __init__(self, question: str, detail: str = "", confirm: str = "Move to trash") -> None:
        super().__init__()
        self.question = question
        self.detail = detail
        self.confirm = confirm

    def compose(self) -> ComposeResult:
        with DialogBox(id="box") as box:
            box.border_title = "Confirm"
            yield Label(self.question)
            if self.detail:
                yield Label(self.detail, id="hint")
            yield from self.buttons(confirm=self.confirm, variant="error")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_next(self) -> None:
        self.focus_next()

    def action_previous(self) -> None:
        self.focus_previous()

    def action_no(self) -> None:
        self.dismiss(False)
