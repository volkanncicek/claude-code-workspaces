"""Opportunistic snapshots of the live set. No daemon — each run that holds live sessions records them. Keep a short history so a crash cannot overwrite the set being recovered."""

from datetime import UTC, datetime
from pathlib import Path

from . import store
from .live import LiveSession
from .paths import snapshots_dir

KEEP = 20


def take(sessions: list[LiveSession]) -> Path | None:
    """Record the live set. Never raises; skips empty sets (empty looks like a crash)."""
    if not sessions:
        return None
    taken_at = datetime.now(tz=UTC)
    # Exactly what `restore` reads back and nothing more: a snapshot is a recovery record, not a log of the live set.
    payload = {
        "takenAt": taken_at.isoformat(),
        "sessions": [{"sessionId": session.session_id, "cwd": str(session.cwd), "name": session.name} for session in sessions],
    }
    # Milliseconds in the name so two quick snapshots do not collide.
    target = snapshots_dir() / f"{taken_at.strftime('%Y%m%dT%H%M%S.%f')[:-3]}Z.json"
    if not store.write_json(target, payload):
        return None
    _prune()
    return target


def _prune() -> None:
    try:
        existing = sorted(snapshots_dir().glob("*.json"))
        for stale in existing[:-KEEP]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _taken_at(recorded: object, candidate: Path) -> datetime:
    if isinstance(recorded, str):
        try:
            return datetime.fromisoformat(recorded)
        except ValueError:
            pass
    return datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)


def latest_populated() -> tuple[datetime, list[dict]] | None:
    """Most recent non-empty snapshot and when it was taken."""
    try:
        candidates = sorted(snapshots_dir().glob("*.json"), reverse=True)
    except OSError:
        return None
    for candidate in candidates:
        payload = store.read_json(candidate)
        if not isinstance(payload, dict):
            continue
        sessions = payload.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            continue
        return _taken_at(payload.get("takenAt"), candidate), [entry for entry in sessions if isinstance(entry, dict) and entry.get("sessionId")]
    return None
