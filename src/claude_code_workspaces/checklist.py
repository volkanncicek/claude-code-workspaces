"""Checklist shown before restore opens any panes.

Risky entries start unchecked; footer hints are the available actions.
"""

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, SelectionList, Static
from textual.widgets.selection_list import Selection

from .formatting import age
from .restore import DEFAULT_PANE_CAP, RestoreEntry, RestorePlan

_SOURCE_GLYPH = {"workspace": "#", "snapshot": "*", "heuristic": "?"}


def prompt_for(entry: RestoreEntry) -> str:
    glyph = _SOURCE_GLYPH.get(entry.source, " ")
    parts = [
        f"{glyph} {entry.label:<32.32}",
        f"{entry.root.name:<24.24}",
        f"{age(entry.last_active):>9}",
    ]
    if entry.live:
        parts.append("already running")
    elif entry.missing:
        parts.append("transcript gone")
    elif entry.trust and entry.trust.prompts:
        parts.append("will prompt for trust")
    return "  ".join(parts)


class RestoreChecklist(ModalScreen[list[str] | None]):
    """Returns session ids to restore, or `None` on cancel. A Screen so the session list can push it."""

    CSS = """
    Vertical { height: 1fr; }
    SelectionList { height: 1fr; border: round $accent; padding: 0 1; }
    #notes { padding: 0 1; color: $text-muted; height: auto; }
    """

    # `enter` unavailable: SelectionList consumes it for toggle.
    BINDINGS = [
        ("r", "restore", "Restore checked"),
        ("space", "toggle_current", "Check / uncheck"),
        ("a", "select_all", "Check all restorable"),
        ("n", "select_none", "Uncheck all"),
        ("escape,q", "cancel", "Cancel"),
    ]

    def __init__(self, plan: RestorePlan, *, cap: int = DEFAULT_PANE_CAP, title: str | None = None) -> None:
        super().__init__()
        self.plan = plan
        self.cap = cap
        self.heading = title

    def compose(self) -> ComposeResult:
        with Vertical():
            preselected = set(self.plan.default_selection(cap=self.cap))
            yield SelectionList[str](
                *[
                    Selection(
                        prompt_for(entry),
                        entry.session_id,
                        entry.session_id in preselected,
                        disabled=not entry.restorable,
                    )
                    for entry in self.plan.entries
                ]
            )
            yield Static(self._notes(), id="notes")
        yield Footer()

    def on_mount(self) -> None:
        selection_list = self.query_one(SelectionList)
        taken = self.plan.snapshot_taken_at
        if self.heading:
            selection_list.border_title = self.heading
        else:
            selection_list.border_title = f"Restore — snapshot from {taken:%Y-%m-%d %H:%M} UTC" if taken else "Restore — from transcript times"
        glyphs = "   ".join(f"{glyph} {source}" for source, glyph in _SOURCE_GLYPH.items() if any(entry.source == source for entry in self.plan.entries))
        selection_list.border_subtitle = f"{glyphs}   cap {self.cap} panes"

    def _notes(self) -> str:
        lines = list(self.plan.notes)
        prompts = self.plan.trust_prompts(self.plan.default_selection(cap=self.cap))
        if prompts:
            lines.append(f"{len(prompts)} trust prompt(s) expected: {', '.join(root.name for root in prompts)}")
        return "\n".join(lines) or "Nothing to warn about."

    def action_toggle_current(self) -> None:
        self.query_one(SelectionList).action_select()

    def action_select_all(self) -> None:
        selection_list = self.query_one(SelectionList)
        selection_list.deselect_all()
        for entry in self.plan.entries:
            if entry.restorable:
                selection_list.select(entry.session_id)

    def action_select_none(self) -> None:
        self.query_one(SelectionList).deselect_all()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_restore(self) -> None:
        chosen = self.query_one(SelectionList).selected
        if not chosen:
            self.notify("Nothing is checked.", severity="warning")
            return
        if len(chosen) > self.cap:
            self.notify(
                f"{len(chosen)} panes exceeds the cap of {self.cap}. Uncheck some, or raise it with --limit.",
                severity="error",
            )
            return
        self.dismiss(list(chosen))


class _ChecklistApp(App[list[str] | None]):
    """Standalone app wrapper for `ccw restore` when no session list is running."""

    def __init__(self, plan: RestorePlan, *, cap: int, title: str | None) -> None:
        super().__init__()
        self._screen_args = (plan, cap, title)

    def on_mount(self) -> None:
        plan, cap, title = self._screen_args
        self.push_screen(RestoreChecklist(plan, cap=cap, title=title), callback=self.exit)


def choose(plan: RestorePlan, *, cap: int = DEFAULT_PANE_CAP, title: str | None = None) -> list[str] | None:
    """Entry point when no Textual app is running yet."""
    return _ChecklistApp(plan, cap=cap, title=title).run()
