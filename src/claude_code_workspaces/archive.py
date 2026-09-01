"""Hide sessions from the list by recording ids under `~/.ccw/` — nothing under `~/.claude/` is moved or deleted."""

from . import store
from .paths import archive_file


def archived() -> set[str]:
    """Archived ids. Unreadable file → empty set (must not hide sessions)."""
    payload = store.read_json(archive_file())
    if not isinstance(payload, list):
        return set()
    return {entry for entry in payload if isinstance(entry, str) and entry}


def _write(ids: set[str]) -> bool:
    return store.write_json(archive_file(), sorted(ids))


def toggle(session_id: str) -> bool | None:
    """Archive or unarchive. Returns new archived state, or `None` if the write failed."""
    ids = archived()
    now_archived = session_id not in ids
    if not _write(ids | {session_id} if now_archived else ids - {session_id}):
        return None
    return now_archived


def forget_one(session_id: str) -> None:
    """Unarchive without reporting. `service` calls this to hold archive and keep apart; a session that was not archived is not an event."""
    ids = archived()
    if session_id in ids:
        _write(ids - {session_id})


def forget(session_ids: set[str]) -> None:
    """Keep only ids that still exist."""
    ids = archived()
    remaining = ids & session_ids
    if remaining != ids:
        _write(remaining)
