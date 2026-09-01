"""Live sessions from `claude agents --json` — the supported source for what is running now."""

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import shutdown

_in_flight: set[subprocess.Popen] = set()


@dataclass(slots=True, frozen=True)
class LiveSession:
    """One running session as `claude agents --json` reports it. Frozen: a reading of the outside world, never edited."""

    session_id: str
    cwd: Path
    status: str
    name: str | None  # Raw agent handle; may be one Claude generated. Never a display label — see `formatting.session_label`.
    started_at: datetime


class ClaudeUnavailable(RuntimeError):
    """`claude` missing, failed, or returned a non-array payload."""


def abandon_in_flight() -> None:
    """Kill in-flight `claude agents` reads so quit need not wait for them. New spawns are blocked via `shutdown.requested()`."""
    for process in list(_in_flight):
        with contextlib.suppress(OSError):
            process.kill()


def _executable() -> str:
    found = shutil.which("claude")
    if found is None:
        raise ClaudeUnavailable("`claude` was not found on PATH.")
    return found


def live_sessions(*, timeout: float = 30.0) -> list[LiveSession]:
    """What is running now. Deliberately without `--all`: completed background sessions carry a `state` this parser does not read, and a failed job would paint as idle."""
    if shutdown.requested():
        raise ClaudeUnavailable("The session list is closing.")
    argv = [_executable(), "agents", "--json"]
    try:
        # `claude` writes UTF-8; `text=True` alone would decode it through the locale codepage (cp1252 here), which turns a Turkish agent name into mojibake rather than failing.
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ClaudeUnavailable(f"`claude agents --json` could not be started: {exc}") from exc
    _in_flight.add(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ClaudeUnavailable(f"`claude agents --json` did not return within {timeout:g}s.") from exc
    finally:
        _in_flight.discard(process)
    if process.returncode != 0:
        detail = (stderr or stdout).strip().splitlines()
        raise ClaudeUnavailable(f"`claude agents --json` exited {process.returncode}: {detail[-1] if detail else 'no output'}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeUnavailable(f"`claude agents --json` did not return JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ClaudeUnavailable(f"Expected a JSON array of sessions, got {type(payload).__name__}.")
    return [_parse(entry) for entry in payload if isinstance(entry, dict) and entry.get("sessionId")]


def try_live_sessions() -> tuple[list[LiveSession] | None, str | None]:
    """`(sessions, None)` or `(None, reason)`. Prefer this over raising — unreadability is a UI state."""
    try:
        return live_sessions(), None
    except ClaudeUnavailable as exc:
        return None, str(exc)


def _parse(entry: dict) -> LiveSession:
    started = entry.get("startedAt")
    return LiveSession(
        session_id=entry["sessionId"],
        cwd=Path(entry.get("cwd") or Path.home()),
        status=entry.get("status") or "idle",
        name=entry.get("name") or None,
        started_at=datetime.fromtimestamp(started / 1000, tz=UTC) if isinstance(started, (int, float)) else datetime.now(tz=UTC),
    )
