"""Only module that parses `~/.claude/projects/**/*.jsonl`.

Format is internal/unstable — best-effort, never raises; on failure the tool runs on live data alone. Session transcripts are one directory deep; deeper files are sub-agents. Metadata comes from each file's head.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import shutdown, store
from .paths import claude_projects, heads_cache


@dataclass(slots=True)
class HistoricalSession:
    """Transcript on disk. Fields other than id/transcript/modified may be None when JSONL parsing degrades.

    Mutable on purpose: `_absorb` fills it record by record as the head is read, so it is a parser accumulator rather than a finished value. Everything downstream copies out of it and never holds it.
    """

    session_id: str
    transcript: Path
    modified: datetime
    size: int
    cwd: Path | None = None
    title: str | None = None
    first_prompt: str | None = None
    branch: str | None = None


HEAD_RECORDS = 200
# Bump when the cached entry shape changes. A mismatch throws the file away rather than reading keys that no longer mean what they did.
HEADS_CACHE_VERSION = 3
_MAX_ERRORS = 50

_ERRORS: list[str] = []
# Mutated in place like `_ERRORS` above rather than rebound, so no function here needs a `global` statement to record how the pass went.
_LAST_PASS = {"complete": False}


def errors() -> list[str]:
    """Failures from the most recent `historical_sessions()` pass. Cleared at the start of each pass, so the count a caller shows describes the reading it is showing rather than the worst moment since launch."""
    return list(_ERRORS)


def scan_was_complete() -> bool:
    """Whether the last `historical_sessions()` pass saw every transcript.

    False when `~/.claude/projects` could not be read, when a file was skipped, or when the pass was cut short. Anything that prunes by absence has to ask first: a pass that saw nothing is not evidence that nothing is there, and one antivirus lock would otherwise erase every archived id at once. `keep.py:5` sidesteps the question by never pruning; archive does prune, so it checks.
    """
    return _LAST_PASS["complete"]


def _note(path: Path, exc: Exception) -> None:
    if len(_ERRORS) < _MAX_ERRORS:
        _ERRORS.append(f"{path.name}: {type(exc).__name__}: {exc}")


def transcript_path(session_id: str) -> Path | None:
    try:
        for candidate in claude_projects().glob(f"*/{session_id}.jsonl"):
            return candidate
    except OSError as exc:
        _note(claude_projects(), exc)
    return None


def transcript_paths() -> list[Path] | None:
    """Every session transcript, or `None` when the directory itself could not be read. `None` is not `[]`: a caller that prunes by absence has to be able to tell "nothing is there" from "I could not look"."""
    try:
        return sorted(claude_projects().glob("*/*.jsonl"))
    except OSError as exc:
        _note(claude_projects(), exc)
        return None


def paths_by_session_id() -> dict[str, Path]:
    """Index for lookups only. A failed scan reads as an empty index here, which costs a restore plan some detail and nothing else; `scan_was_complete` is what pruning asks."""
    return {path.stem: path for path in transcript_paths() or []}


def _modified(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def last_activity(path: Path) -> datetime | None:
    """mtime even when contents are unparseable (restore heuristic)."""
    try:
        return _modified(path)
    except OSError as exc:
        _note(path, exc)
        return None


# Every codepoint Unicode calls Cc: C0, DEL and the C1 block, which a terminal reads as escape sequences rather than as text. They are removed rather than replaced with a visible marker such as `<ESC>`, because these strings are shown in fixed-width table cells where a marker would cost more room than the words it displaced, and a title is decoration rather than evidence — nothing downstream is deciding anything by it. The five whitespace controls become a space instead, so a title broken over two lines does not come back with its words run together; the rest have no width to preserve. Only Cc is touched: Cf is left alone because the zero-width joiner that emoji sequences are built from lives there, and every letter outside ASCII is untouched, so Turkish and other scripts come through byte for byte.
_CONTROLS = {codepoint: (" " if chr(codepoint) in "\t\n\v\f\r" else None) for codepoint in (*range(0x20), 0x7F, *range(0x80, 0xA0))}


def _display(value: object) -> str | None:
    """Text from a transcript, safe to print and safe to put in a widget. Anything that is not a string reads as absent, because the format is unstable and a cached head is loosely typed by the time it comes back off disk."""
    if not isinstance(value, str):
        return None
    return value.translate(_CONTROLS).strip() or None


def _text(content: object) -> str | None:
    if isinstance(content, str):
        return _display(content)
    if isinstance(content, list):
        parts = [text for block in content if isinstance(block, dict) and block.get("type") == "text" and isinstance(text := block.get("text"), str) and text]
        return _display("\n".join(parts))
    return None


def _is_human_prompt(record: dict) -> bool:
    # Presence of toolUseResult, not truthiness — empty tool results are still tool results.
    return record.get("type") == "user" and not record.get("isSidechain") and record.get("userType") == "external" and "toolUseResult" not in record


def scan(path: Path, *, head: int = HEAD_RECORDS) -> HistoricalSession | None:
    try:
        session = HistoricalSession(
            session_id=path.stem,
            transcript=path,
            modified=_modified(path),
            size=path.stat().st_size,
        )
    except OSError as exc:
        _note(path, exc)
        return None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= head:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                _absorb(session, record)
                # Stop once title/prompt/cwd are known; do not wait for a real branch (HEAD-only files would scan the whole head).
                if session.title and session.first_prompt and session.cwd:
                    break
    except OSError as exc:
        _note(path, exc)
    if session.branch == "HEAD":
        # Claude's placeholder for a detached head, not a branch name. Normalised here so nothing downstream has to know the placeholder exists.
        session.branch = None
    return session


def _absorb(session: HistoricalSession, record: dict) -> None:
    kind = record.get("type")
    if kind == "ai-title" and not session.title:
        session.title = _display(record.get("aiTitle"))
        return
    # The cwd is deliberately not put through `_display`: it is a real path that the launcher sets a pane to, so a rewritten one would resume the session in the wrong directory or in none at all. Windows and POSIX both forbid control characters in a path component, so there is nothing here to strip that a working directory could legitimately contain.
    if session.cwd is None and isinstance(record.get("cwd"), str):
        session.cwd = Path(record["cwd"])
    branch = _display(record.get("gitBranch"))
    # HEAD is a placeholder and may precede the real branch in the same session, so it is accepted here and dropped by `scan` at the end.
    if branch and session.branch in (None, "HEAD"):
        session.branch = branch
    if session.first_prompt is None and _is_human_prompt(record):
        message = record.get("message")
        if isinstance(message, dict):
            session.first_prompt = _text(message.get("content"))


def _stat(path: Path) -> tuple[datetime, int] | None:
    try:
        info = path.stat()
    except OSError as exc:
        _note(path, exc)
        return None
    return datetime.fromtimestamp(info.st_mtime, tz=UTC), info.st_size


def _restored(session_id: str, path: Path, modified: datetime, size: int, remembered: dict) -> HistoricalSession | None:
    """Reuse a cached head when size *and* modification time are unchanged.

    Size alone would be enough for an append-only file, but a transcript rewritten in place can land on the same byte count, and the stale title, cwd and first prompt would then be believed for the life of the install.
    """
    entry = remembered.get(session_id)
    if not isinstance(entry, dict) or entry.get("size") != size or entry.get("modified") != modified.isoformat():
        return None
    cwd = entry.get("cwd")
    return HistoricalSession(
        session_id=session_id,
        transcript=path,
        modified=modified,
        size=size,
        cwd=Path(cwd) if isinstance(cwd, str) else None,
        # Cleaned again on the way out, not only where the transcript is read: an entry written before this module stripped control characters is still on disk, and it is served from here without the transcript ever being opened again.
        title=_display(entry.get("title")),
        first_prompt=_display(entry.get("firstPrompt")),
        branch=_display(entry.get("branch")),
    )


def _remembered_heads() -> dict:
    """Cached heads, or nothing at all when the file is unreadable or was written by an older shape.

    Loosely typed on purpose: this came off disk, so `_restored` validates each entry rather than trusting a shape the annotation cannot prove.
    """
    payload = store.read_json(heads_cache())
    if not isinstance(payload, dict) or payload.get("version") != HEADS_CACHE_VERSION:
        return {}
    heads = payload.get("heads")
    return heads if isinstance(heads, dict) else {}


def historical_sessions() -> list[HistoricalSession]:
    """All transcripts, newest first. Heads are cached; size and modification time decide whether to rescan."""
    _ERRORS.clear()
    _LAST_PASS["complete"] = False
    paths = transcript_paths()
    if paths is None:
        return []
    remembered = _remembered_heads()
    sessions: list[HistoricalSession] = []
    learned: dict[str, dict] = {}
    skipped = False
    for path in paths:
        if shutdown.requested():
            # Do not persist a partial pass — prune would treat unseen ids as gone.
            return sessions
        stamped = _stat(path)
        if stamped is None:
            skipped = True
            continue
        modified, size = stamped
        session = _restored(path.stem, path, modified, size, remembered)
        if session is None:
            session = scan(path)
            if session is None:
                skipped = True
                continue
            learned[session.session_id] = {
                "size": session.size,
                "modified": session.modified.isoformat(),
                "cwd": str(session.cwd) if session.cwd else None,
                "title": session.title,
                "firstPrompt": session.first_prompt,
                "branch": session.branch,
            }
        sessions.append(session)
    _LAST_PASS["complete"] = not skipped
    on_disk = {session.session_id for session in sessions}
    keeping = {**{key: value for key, value in remembered.items() if key in on_disk}, **learned}
    if keeping != remembered:
        store.write_json(heads_cache(), {"version": HEADS_CACHE_VERSION, "heads": keeping})
    sessions.sort(key=lambda session: session.modified, reverse=True)
    return sessions
