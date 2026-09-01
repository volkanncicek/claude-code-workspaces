"""Sessions the user set aside, recorded under `~/.ccw/` — nothing under `~/.claude/` is moved or deleted.

`archive.py`'s shape for the opposite intention: that one means "out of my way", this one means "do not lose this". They are mutually exclusive, and `service` is the only place that knows it, so neither module imports the other.

Deliberately missing `archive.forget`: a kept id whose transcript has gone stays kept. Dropping a mark nobody asked for is cheap; dropping one they did is not.
"""

from . import store
from .paths import kept_file


def kept() -> set[str]:
    """Kept ids. Unreadable file → empty set (must not invent marks)."""
    payload = store.read_json(kept_file())
    if not isinstance(payload, list):
        return set()
    return {entry for entry in payload if isinstance(entry, str) and entry}


def _write(ids: set[str]) -> bool:
    return store.write_json(kept_file(), sorted(ids))


def toggle(session_id: str) -> bool | None:
    """Keep or release. Returns the new state, or `None` if the write failed."""
    ids = kept()
    now_kept = session_id not in ids
    if not _write(ids | {session_id} if now_kept else ids - {session_id}):
        return None
    return now_kept


def drop(session_id: str) -> None:
    """Release without reporting. `service` calls this to hold keep and archive apart; a mark that was not there is not an event."""
    ids = kept()
    if session_id in ids:
        _write(ids - {session_id})
