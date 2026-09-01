"""Named workspaces: fixed lists of session ids saved under a name.

Contents are frozen at save time; refresh and add/remove keep the list maintainable, and missing transcripts are reported rather than dropped.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from . import store
from .live import LiveSession
from .paths import trash_dir, workspaces_dir

# Narrow: a workspace name is also a filename and must be typeable without quoting.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# Bump when the file's shape changes. A workspace is the one thing here a user cannot regenerate, so a shape change must fail loudly rather than read as an empty set.
FILE_VERSION = 1

# Deleted workspaces kept in `~/.ccw/trash/`. Same shape as `snapshot.KEEP`: a bounded history, not an archive.
KEEP_TRASHED = 20


class WorkspaceError(RuntimeError):
    """User-facing message for a workspace that could not be read, written or found. Kept as the base of the three below so `except WorkspaceError` still catches every workspace failure."""


class WorkspaceRequestError(WorkspaceError):
    """The request itself cannot stand: an unusable name, or a name already taken. Asking again with different words is the way out."""


class WorkspaceNotFound(WorkspaceError):
    """Nothing exists under the name given."""


class WorkspaceStoreError(WorkspaceError):
    """`~/.ccw/` could not be read or written: a full disk, a permission failure, or a file that is no longer a workspace. The machine failed, not the request, so retrying under another name changes nothing."""


@dataclass(slots=True, frozen=True)
class Member:
    session_id: str
    cwd: Path
    name: str | None

    @classmethod
    def of(cls, session: LiveSession) -> Self:
        return cls(session.session_id, session.cwd, session.name)


@dataclass(slots=True)
class Workspace:
    # `name` is user-chosen and is also the filename. A workspace's name is a name in the ordinary sense; a session's `name` is an agent handle. Different domains, deliberately both `name`.
    name: str
    members: list[Member]
    created: datetime
    updated: datetime

    @property
    def session_ids(self) -> list[str]:
        return [member.session_id for member in self.members]


def validate(name: str) -> str:
    if not NAME_PATTERN.match(name):
        raise WorkspaceRequestError(f"'{name}' is not a usable workspace name. Use lower-case letters, digits, dot, dash or underscore, starting with a letter or digit.")
    return name


def path_for(name: str) -> Path:
    return workspaces_dir() / f"{validate(name)}.json"


def exists(name: str) -> bool:
    return path_for(name).is_file()


def from_members(name: str, members: list[Member]) -> Workspace:
    """A workspace from members the caller already built. `from_live` is the case where they come from `claude agents`; anything carrying an id and a cwd can use this."""
    now = datetime.now(tz=UTC)
    return Workspace(name=validate(name), members=list(members), created=now, updated=now)


def from_live(name: str, sessions: list[LiveSession]) -> Workspace:
    return from_members(name, [Member.of(session) for session in sessions])


def save(workspace: Workspace) -> Path:
    target = path_for(workspace.name)
    payload = {
        "version": FILE_VERSION,
        "name": workspace.name,
        "created": workspace.created.isoformat(),
        "updated": workspace.updated.isoformat(),
        # `sessionId` inside an entry stays camelCase: that identifier is Claude's, and it is the same string `claude agents --json` reports.
        "members": [{"sessionId": member.session_id, "cwd": str(member.cwd), "name": member.name} for member in workspace.members],
    }
    if not store.write_json(target, payload):
        raise WorkspaceStoreError(f"Could not write '{workspace.name}'. Check that {workspaces_dir()} is writable.")
    return target


def load(name: str) -> Workspace:
    path = path_for(name)
    if not path.is_file():
        raise WorkspaceNotFound(f"No workspace called '{name}'. `ccw list` shows the ones that exist.")
    payload = store.read_json(path)
    if payload is None:
        raise WorkspaceStoreError(f"'{name}' could not be read. It is missing, unreadable, or not JSON.")
    return _parse(name, payload)


def _parse(name: str, payload: object) -> Workspace:
    if not isinstance(payload, dict):
        raise WorkspaceStoreError(f"'{name}' is not a workspace file.")
    version = payload.get("version")
    if version != FILE_VERSION:
        raise WorkspaceStoreError(f"'{name}' was written by a different version of ccw (file version {version!r}, expected {FILE_VERSION}). Save it again to rewrite it in the current shape.")
    recorded = payload.get("members")
    if not isinstance(recorded, list):
        raise WorkspaceStoreError(f"'{name}' holds no member list.")
    members: list[Member] = []
    for entry in recorded:
        # Validate each field — a hand-edited or half-written file should drop bad entries, not the whole set.
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            continue
        cwd = entry.get("cwd")
        member_name = entry.get("name")
        members.append(Member(session_id=session_id, cwd=Path(cwd) if isinstance(cwd, str) and cwd else Path.home(), name=member_name if isinstance(member_name, str) and member_name else None))
    stored_name = payload.get("name")
    return Workspace(
        name=stored_name if isinstance(stored_name, str) and stored_name else name,
        members=members,
        created=_timestamp(payload.get("created")),
        updated=_timestamp(payload.get("updated")),
    )


def _timestamp(value: object) -> datetime:
    """Always aware. A hand-edited or externally written file can carry an offset-less stamp, and one naive value among aware ones makes `load_all`'s sort raise, which takes down the whole listing rather than skipping the one bad file."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def load_all() -> list[Workspace]:
    """Most recently updated first. Skips unreadable files rather than failing the listing."""
    try:
        paths = sorted(workspaces_dir().glob("*.json"))
    except OSError:
        return []
    found: list[Workspace] = []
    for path in paths:
        try:
            found.append(load(path.stem))
        except WorkspaceError:
            continue
    found.sort(key=lambda workspace: workspace.updated, reverse=True)
    return found


def rename(old: str, new: str) -> Path:
    workspace = load(old)
    target = path_for(new)
    if target.exists():
        raise WorkspaceRequestError(f"'{new}' already exists.")
    workspace.name = validate(new)
    workspace.updated = datetime.now(tz=UTC)
    saved = save(workspace)
    path_for(old).unlink(missing_ok=True)
    return saved


def delete(name: str) -> Path:
    """Move to `~/.ccw/trash/`, never unlink. Recoverable with `untrash` until pruned."""
    source = path_for(name)
    if not source.is_file():
        raise WorkspaceNotFound(f"No workspace called '{name}'.")
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    target = trash_dir() / f"{name}.{stamp}.json"
    try:
        trash_dir().mkdir(parents=True, exist_ok=True)
        source.replace(target)
    except OSError as exc:
        raise WorkspaceStoreError(f"Could not move '{name}' to the trash: {exc}") from exc
    _prune_trash()
    return target


@dataclass(slots=True, frozen=True)
class Trashed:
    """A deleted workspace still on disk. `name` is what it was called; `path` is where it went."""

    path: Path
    name: str
    deleted_at: datetime
    members: int


def trashed() -> list[Trashed]:
    """Recoverable deletions, newest first. Skips unreadable files rather than failing the listing."""
    try:
        paths = sorted(trash_dir().glob("*.json"))
    except OSError:
        return []
    found: list[Trashed] = []
    for path in paths:
        payload = store.read_json(path)
        if not isinstance(payload, dict) or payload.get("version") != FILE_VERSION:
            continue
        recorded = payload.get("members")
        stored_name = payload.get("name")
        try:
            deleted_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        found.append(
            Trashed(
                path=path,
                name=stored_name if isinstance(stored_name, str) and stored_name else path.stem,
                deleted_at=deleted_at,
                members=len(recorded) if isinstance(recorded, list) else 0,
            )
        )
    found.sort(key=lambda item: item.deleted_at, reverse=True)
    return found


def untrash(name: str) -> Path:
    """Put the most recently deleted workspace called `name` back. Refuses to overwrite a live one — that would be a silent loss, which is the thing the trash exists to prevent."""
    validate(name)
    candidates = [item for item in trashed() if item.name == name]
    if not candidates:
        raise WorkspaceNotFound(f"Nothing called '{name}' is in the trash. `ccw trash` lists what is.")
    target = path_for(name)
    if target.exists():
        raise WorkspaceRequestError(f"'{name}' already exists. Rename or delete that one before restoring the trashed one.")
    try:
        candidates[0].path.replace(target)
    except OSError as exc:
        raise WorkspaceStoreError(f"Could not restore '{name}': {exc}") from exc
    return target


def _prune_trash() -> None:
    """Keep the most recent `KEEP_TRASHED`. Nothing else ever removes from the trash, so without this it is a disk leak that grows for the life of the install."""
    try:
        existing = sorted(trash_dir().glob("*.json"), key=lambda path: path.stat().st_mtime)
        for stale in existing[:-KEEP_TRASHED]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def refresh(name: str, sessions: list[LiveSession]) -> Workspace:
    workspace = load(name)
    workspace.members = [Member.of(session) for session in sessions]
    workspace.updated = datetime.now(tz=UTC)
    save(workspace)
    return workspace


def add_members(name: str, members: list[Member]) -> tuple[Workspace, list[str]]:
    """Ignores ids already present; returns the workspace and newly added ids."""
    workspace = load(name)
    present = set(workspace.session_ids)
    added = [member for member in members if member.session_id not in present]
    workspace.members.extend(added)
    if added:
        workspace.updated = datetime.now(tz=UTC)
        save(workspace)
    return workspace, [member.session_id for member in added]


def remove(name: str, session_ids: list[str]) -> tuple[Workspace, list[str]]:
    """Returns the workspace and the ids that were actually present."""
    workspace = load(name)
    wanted = set(session_ids)
    dropped = [member.session_id for member in workspace.members if member.session_id in wanted]
    if dropped:
        workspace.members = [member for member in workspace.members if member.session_id not in wanted]
        workspace.updated = datetime.now(tz=UTC)
        save(workspace)
    return workspace, dropped
