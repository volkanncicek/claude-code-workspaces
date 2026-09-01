"""One merged session list: live status from `claude agents`, content/labels from transcripts."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import archive, keep, transcripts
from . import live as live_source
from .formatting import session_label
from .live import LiveSession
from .repos import git_root, git_roots
from .transcripts import HistoricalSession

STATUS_GLYPH = {"waiting": "!", "busy": ">", "idle": "-"}
HISTORICAL_GLYPH = " "


@dataclass(slots=True)
class SessionRow:
    session_id: str
    cwd: Path
    root: Path
    title: str | None  # Transcript ai-title.
    agent_name: str | None  # Raw handle from `claude agents`; `label` decides whether it is worth showing.
    archived: bool  # Id recorded under ~/.ccw; nothing moved.
    status: str | None  # None when not running.
    last_active: datetime | None
    transcript: Path | None
    branch: str | None
    first_prompt: str | None
    kept: bool = False  # Set aside by the user. Defaulted so existing constructions are untouched.
    _haystack: str | None = field(default=None, repr=False, compare=False)

    @property
    def live(self) -> bool:
        return self.status is not None

    @property
    def glyph(self) -> str:
        return STATUS_GLYPH.get(self.status or "", HISTORICAL_GLYPH)

    @property
    def label(self) -> str:
        return session_label(agent_name=self.agent_name, cwd=self.cwd, title=self.title, session_id=self.session_id)

    def adopt(self, session: LiveSession | None) -> None:
        """Fold a live reading, or its absence, into this row.

        A method rather than three assignments at the call site: `haystack` is cached from `agent_name`, so the two have to move together or search silently keeps matching the old name.
        """
        self.status = session.status if session else None
        if session is not None and self.agent_name != session.name:
            self.agent_name = session.name
            self._haystack = None

    @property
    def resume_command(self) -> str:
        return f"claude --resume {self.session_id}"

    @property
    def haystack(self) -> str:
        """Search blob, cached. Cleared when `merge_live` updates `agent_name`."""
        if self._haystack is None:
            parts = [self.session_id, self.title or "", self.agent_name or "", str(self.cwd), self.branch or "", self.first_prompt or ""]
            self._haystack = " ".join(parts).casefold()
        return self._haystack


def history_rows() -> list[SessionRow]:
    """Transcript rows only (no live status). Warm git roots in one batch before building rows."""
    history = transcripts.historical_sessions()
    git_roots([session.cwd for session in history if session.cwd])
    archived = archive.archived()
    shelved = keep.kept()
    rows = [_from_history(session, archived, shelved) for session in history]
    return ordered(rows)


def prune_archive(rows: list[SessionRow]) -> None:
    """Drop archived ids that no longer exist. Explicit write — assembling rows must not mutate disk.

    Only after a complete transcript pass: rows built from a scan that failed or was cut short list what could be read, not what exists, and pruning against them would erase every archived id over one permission blip. `keep.py:5` refuses to prune at all for the same reason.
    """
    if not transcripts.scan_was_complete():
        return
    archive.forget({row.session_id for row in rows})


def collect() -> tuple[list[SessionRow], str | None]:
    """Full list in one call. The TUI paints `history_rows` then `merge_live` instead; keep that path equivalent to this."""
    running, note = live_source.try_live_sessions()
    rows = merge_live(history_rows(), running)
    prune_archive(rows)
    return rows, note


def merge_live(rows: list[SessionRow], live: list[LiveSession] | None) -> list[SessionRow]:
    """Fold live status into existing rows. `None` clears status (read failed ≠ empty). New live-only sessions are appended without a full rebuild."""
    if live is None:
        for row in rows:
            row.adopt(None)
        return ordered(rows)

    fresh = {session.session_id: session for session in live}
    known = {row.session_id for row in rows}
    for row in rows:
        row.adopt(fresh.get(row.session_id))
    appeared = [session for session_id, session in fresh.items() if session_id not in known]
    if appeared:
        archived = archive.archived()
        shelved = keep.kept()
        rows = rows + [_from_live(session, archived, shelved) for session in appeared]
    return ordered(rows)


def ordered(rows: list[SessionRow]) -> list[SessionRow]:
    """Live first because it needs you now, then the shelf, then everything else by recency. The middle term is what makes a keep mark a place rather than a decoration.

    Public because keeping a session changes where it belongs, and the TUI has to be able to ask for the answer without waiting for the next reload. This is the one ordering policy; nothing else sorts rows.
    """
    return sorted(rows, key=lambda row: (not row.live, not row.kept, -(row.last_active.timestamp() if row.last_active else 0)))


def _from_live(session: LiveSession, archived: set[str], shelved: set[str]) -> SessionRow:
    """Row for a live session missing from the transcript scan; scan its one file for a title."""
    path = transcripts.transcript_path(session.session_id)
    history = transcripts.scan(path) if path else None
    return SessionRow(
        session_id=session.session_id,
        cwd=session.cwd,
        root=git_root(session.cwd),
        title=history.title if history else None,
        agent_name=session.name,
        archived=session.session_id in archived,
        kept=session.session_id in shelved,
        status=session.status,
        last_active=history.modified if history else session.started_at,
        transcript=path,
        branch=history.branch if history else None,
        first_prompt=history.first_prompt if history else None,
    )


def _from_history(history: HistoricalSession, archived: set[str], shelved: set[str]) -> SessionRow:
    cwd = history.cwd or Path.home()
    root = git_root(cwd)
    return SessionRow(
        session_id=history.session_id,
        cwd=cwd,
        root=root,
        title=history.title,
        agent_name=None,
        archived=history.session_id in archived,
        kept=history.session_id in shelved,
        status=None,
        last_active=history.modified,
        transcript=history.transcript,
        branch=history.branch,
        first_prompt=history.first_prompt,
    )


def search(rows: list[SessionRow], query: str) -> list[SessionRow]:
    needle = query.strip().casefold()
    if not needle:
        return rows
    return [row for row in rows if needle in row.haystack]


def waiting_now(previous: dict[str, str], rows: list[SessionRow]) -> list[SessionRow]:
    """Sessions that flipped to `waiting` since `previous` — not ones that were already waiting."""
    return [row for row in rows if row.status == "waiting" and previous.get(row.session_id) != "waiting"]


def statuses(rows: list[SessionRow]) -> dict[str, str]:
    return {row.session_id: row.status for row in rows if row.status}
