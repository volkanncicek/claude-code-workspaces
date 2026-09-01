"""Shared mutations and launches for CLI and TUI.

State changes return an `Outcome` instead of raising, so both surfaces share wording and failure handling. Reads stay outside except `plan_named_restore`, which owns the openable/dead/over-cap decision that had drifted between the two surfaces. Crash-restore selection (checklist and `--json`) stays in the CLI; building or running the launcher argv goes through here.
"""

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import archive, keep, restore, shutdown, snapshot, workspaces
from . import live as live_source
from .launcher import CURRENT_WINDOW, NEW_WINDOW, LauncherUnavailable, Window, default_launcher
from .live import LiveSession
from .restore import DEFAULT_PANE_CAP, RestoreEntry, RestorePlan
from .sessions import SessionRow
from .trust import trust_for, trusted_roots
from .workspaces import Member, Workspace, WorkspaceError, WorkspaceNotFound, WorkspaceStoreError

# Restored panes take ~4s to appear in `claude agents --json`; wait longer so the post-restore snapshot is not empty.
SETTLE_SECONDS = 8.0

# Failure classes carried on a failed `Outcome`. They name what went wrong, not what a shell should do about it: the CLI maps them to exit codes and the TUI ignores them, so this module never picks either.
REFUSED = "refused"  # The request was understood and refused: an unusable name, a name already taken, or a state that contradicts it.
NOT_FOUND = "not-found"  # Nothing exists under the name given: a workspace, a trashed workspace, or a session id.
NOTHING = "nothing"  # The request is fine but there is nothing left to act on: every chosen session is running or has lost its transcript.
UNAVAILABLE = "unavailable"  # The environment cannot do it: the launcher cannot be driven, the live set cannot be read, `~/.ccw` cannot be written.


@dataclass(slots=True, frozen=True)
class Outcome:
    """Shared result: `message` for both surfaces; `lines` optional detail; `kind` the failure class, meaningless when `ok`."""

    ok: bool
    message: str
    lines: tuple[str, ...] = ()
    kind: str = REFUSED


def _failed(message: str, kind: str = REFUSED) -> Outcome:
    return Outcome(ok=False, message=message, kind=kind)


def _kind_for(exc: WorkspaceError) -> str:
    """The failure class a workspace exception belongs to. A store that cannot be written is the environment failing, not the request: reported as a refusal, a caller retries under another name forever instead of reporting a broken machine."""
    if isinstance(exc, WorkspaceStoreError):
        return UNAVAILABLE
    if isinstance(exc, WorkspaceNotFound):
        return NOT_FOUND
    return REFUSED


def _live_or_reason() -> tuple[list[LiveSession] | None, str | None, str]:
    """Live set, or a refusal reason and its class. Empty and unreadable must not collapse into one message: one is a fact about the machine, the other is a gap in what the tool knows."""
    sessions, note = live_source.try_live_sessions()
    if sessions is None:
        return None, note, UNAVAILABLE
    if not sessions:
        return None, "Nothing is running.", REFUSED
    return sessions, None, REFUSED


def _member_lines(workspace: Workspace) -> tuple[str, ...]:
    return tuple(f"{member.name or member.session_id[:8]:<32.32} {member.cwd}" for member in workspace.members)


def save_workspace(name: str, *, force: bool = False) -> Outcome:
    """Validate the name before reading live sessions, so a bad name is cheap."""
    try:
        workspaces.validate(name)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    if workspaces.exists(name) and not force:
        return _failed(f"'{name}' already exists. `ccw refresh {name}` updates it from what is live, or pass --force to replace it.")
    live, reason, kind = _live_or_reason()
    if live is None:
        return _failed(f"{reason} There is nothing to save.", kind)
    try:
        workspace = workspaces.from_live(name, live)
        workspaces.save(workspace)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    snapshot.take(live)  # Live set already in hand; every run should leave a snapshot.
    return Outcome(ok=True, message=f"Saved '{workspace.name}' with {len(workspace.members)} session(s).", lines=_member_lines(workspace))


def refresh_workspace(name: str) -> Outcome:
    try:
        before = set(workspaces.load(name).session_ids)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    live, reason, kind = _live_or_reason()
    if live is None:
        return _failed(f"{reason} Refreshing would empty '{name}'; use `ccw rm` if that is what you want.", kind)
    try:
        updated = workspaces.refresh(name, live)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    snapshot.take(live)
    after = set(updated.session_ids)
    return Outcome(ok=True, message=f"'{updated.name}' now holds {len(updated.members)} session(s): {len(after - before)} added, {len(before - after)} dropped.")


def add_sessions(name: str, session_ids: list[str], pool: list[SessionRow]) -> Outcome:
    """Add from any known session, running or not — a member is an id and a cwd, and a transcript carries both. `pool` comes from the caller because reads live outside this module."""
    try:
        workspaces.load(name)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    wanted = set(session_ids)
    known = [row for row in pool if row.session_id in wanted]
    unknown = wanted - {row.session_id for row in known}
    if unknown:
        return _failed(f"No session on disk or running with that id, so there is nothing to add: {', '.join(sorted(unknown))}", NOT_FOUND)
    try:
        _, added = workspaces.add_members(name, [Member(session_id=row.session_id, cwd=row.cwd, name=row.agent_name) for row in known])
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    return Outcome(ok=True, message=f"Added {len(added)} session(s) to '{name}'." if added else f"'{name}' already held all of those.")


def remove_sessions(name: str, session_ids: list[str]) -> Outcome:
    try:
        _, dropped = workspaces.remove(name, session_ids)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    missing = sorted(set(session_ids) - set(dropped))
    lines = (f"Not in '{name}': {', '.join(missing)}",) if missing else ()
    return Outcome(ok=True, message=f"Dropped {len(dropped)} session(s) from '{name}'.", lines=lines)


def rename_workspace(old: str, new: str) -> Outcome:
    try:
        workspaces.rename(old, new)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    return Outcome(ok=True, message=f"'{old}' is now '{new}'.")


def delete_workspace(name: str) -> Outcome:
    try:
        target = workspaces.delete(name)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    return Outcome(ok=True, message=f"Moved to {target}. The conversations themselves are untouched; `ccw untrash {name}` brings the workspace back.")


def undelete_workspace(name: str) -> Outcome:
    try:
        workspaces.untrash(name)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    return Outcome(ok=True, message=f"'{name}' is back. `ccw list` shows it again.")


@dataclass(slots=True, frozen=True)
class MemberView:
    """A workspace's members resolved for the edit screen. `blocked` is set instead of raising, so the TUI never has to know `WorkspaceError` exists."""

    name: str
    session_ids: tuple[str, ...] = ()
    entries: tuple[RestoreEntry, ...] = ()
    blocked: Outcome | None = None


def view_members(name: str) -> MemberView:
    """Read for the member editor. Blocking, so call it off the message loop."""
    try:
        workspace = workspaces.load(name)
    except WorkspaceError as exc:
        return MemberView(name=name, blocked=_failed(str(exc), _kind_for(exc)))
    plan = restore.plan_for_workspace(workspace)
    return MemberView(name=name, session_ids=tuple(workspace.session_ids), entries=tuple(plan.entries))


def describe_workspace(name: str) -> Outcome:
    """Shared confirmation wording for delete; a read that lives next to the mutation it precedes."""
    try:
        workspace = workspaces.load(name)
    except WorkspaceError as exc:
        return _failed(str(exc), _kind_for(exc))
    return Outcome(ok=True, message=f"'{workspace.name}' holds {len(workspace.members)} session(s):", lines=_member_lines(workspace))


@dataclass(slots=True, frozen=True)
class NamedRestore:
    """Named workspace resolved for restore. `plan` is None only if load failed; otherwise `blocked` is set when nothing can open."""

    name: str
    cap: int
    plan: RestorePlan | None = None
    openable: tuple[str, ...] = ()
    dead: tuple[RestoreEntry, ...] = ()
    blocked: Outcome | None = None

    @property
    def over_cap(self) -> bool:
        return len(self.openable) > self.cap

    @property
    def question(self) -> str:
        return f"Open {len(self.openable)} panes from '{self.name}'?"

    @property
    def detail(self) -> str:
        return f"That is more than the usual {self.cap}. They open all at once and close one at a time."

    @property
    def dead_note(self) -> str:
        return f"{len(self.dead)} session(s) in '{self.name}' no longer have a transcript."


def plan_named_restore(name: str, *, cap: int = DEFAULT_PANE_CAP) -> NamedRestore:
    """Openable ids, dead members, and over-cap flag for a named workspace — one decision for both surfaces."""
    try:
        workspace = workspaces.load(name)
    except WorkspaceError as exc:
        return NamedRestore(name=name, cap=cap, blocked=_failed(str(exc), _kind_for(exc)))
    plan = restore.plan_for_workspace(workspace)
    entries = plan.openable
    dead = tuple(plan.dead)
    if not entries:
        # Prefer plan.notes when the live source failed; otherwise the set is empty, all live, or transcripts are gone. A borrowed note means the environment could not answer, which is a different class from having nothing to open.
        note = plan.notes[0] if plan.notes else None
        reason = note or f"Nothing in '{name}' can be opened: already running, or the transcripts are gone."
        return NamedRestore(name=name, cap=cap, plan=plan, dead=dead, blocked=_failed(reason, UNAVAILABLE if note else NOTHING))
    return NamedRestore(name=name, cap=cap, plan=plan, openable=tuple(entry.session_id for entry in entries), dead=dead)


def _chosen_groups(plan: RestorePlan, session_ids: Iterable[str]) -> tuple[set[str], dict[Path, list[RestoreEntry]]]:
    chosen = {entry.session_id for entry in plan.entries if entry.restorable} & set(session_ids)
    return chosen, plan.panes_for(chosen)


def preview_restore(plan: RestorePlan, session_ids: Iterable[str], *, window: Window = CURRENT_WINDOW) -> Outcome:
    """Build the launcher argv without spawning. Same selection rules as `launch_restore`."""
    chosen, groups = _chosen_groups(plan, session_ids)
    if not groups:
        return _failed("Nothing to open: every chosen session is already running, or its transcript is gone.", NOTHING)
    launcher = default_launcher()
    try:
        argv = launcher.build(groups, window=window)
    except LauncherUnavailable as exc:
        return _failed(f"{launcher.name} cannot be driven here: {exc}", UNAVAILABLE)
    return Outcome(
        ok=True,
        message=f"Would open {len(chosen)} pane(s) across {len(groups)} tab(s) with:",
        # Quoted, not joined: `C:\Program Files\...\wt.exe` and a project under `My Documents` both split at the space otherwise, and the printed line is meant to be runnable. This is a Windows argv, which is what `list2cmdline` encodes.
        lines=(subprocess.list2cmdline(argv),),
    )


def launch_restore(plan: RestorePlan, session_ids: Iterable[str], *, source: str = "", window: Window = CURRENT_WINDOW) -> Outcome:
    """Open the chosen panes and report trust prompts. Caller chooses the set (checklist vs named open-all)."""
    chosen, groups = _chosen_groups(plan, session_ids)
    if not groups:
        return _failed("Nothing to open: every chosen session is already running, or its transcript is gone.", NOTHING)
    launcher = default_launcher()
    try:
        launcher.launch(groups, window=window)
    except LauncherUnavailable as exc:
        return _failed(f"{launcher.name} cannot be driven here: {exc}", UNAVAILABLE)
    prompts = plan.trust_prompts(chosen)
    lines = (f"Expect {len(prompts)} trust prompt(s): {', '.join(str(root) for root in prompts)}",) if prompts else ()
    from_where = f" from '{source}'" if source else ""
    where = "a new window" if window == NEW_WINDOW else "this window"
    return Outcome(ok=True, message=f"Opening {len(chosen)} pane(s) across {len(groups)} tab(s){from_where} in {where}.", lines=lines)


def record_restored_set(*, settle: float | None = None) -> None:
    """Wait for restored panes to register, then snapshot. Never call on the way *into* a restore — that would overwrite the crash record. Blocks; use a worker from a message loop. `settle` overrides the wait for tests."""
    # Interruptible: Textual joins thread workers before `App.run` returns, so a plain sleep here held the whole process open for the rest of the window. The snapshot is lost on quit either way — the live read refuses once `shutdown` is set — so there is nothing worth waiting out.
    if shutdown.wait(SETTLE_SECONDS if settle is None else settle):
        return
    live, _ = live_source.try_live_sessions()
    snapshot.take(live or [])


def resume_session(row: SessionRow, *, fork: bool = False) -> Outcome:
    """Resume or fork one session in the current window. Refusals are stated here so both surfaces share them."""
    if row.live:
        return _failed(f"{row.label} is already running.")
    if row.transcript is None:
        return _failed(f"{row.label} has no transcript to resume.")
    entry = RestoreEntry(
        session_id=row.session_id,
        cwd=row.cwd,
        root=row.root,
        # The row's sources, not `row.label` — `agent_name` holds a raw handle, and a resolved label put there would be resolved twice.
        agent_name=row.agent_name,
        title=row.title,
        source="direct",
        last_active=row.last_active,
        trust=trust_for(row.cwd, trusted_roots()),
        transcript=row.transcript,
    )
    launcher = default_launcher()
    try:
        launcher.launch({row.root: [entry]}, fork=fork, window=CURRENT_WINDOW)
    except LauncherUnavailable as exc:
        return _failed(f"{launcher.name} cannot be driven here: {exc}", UNAVAILABLE)
    message = f"{'Forking' if fork else 'Resuming'} {row.label}."
    if entry.trust and entry.trust.prompts:
        message += " It will ask you to trust the folder first."
    return Outcome(ok=True, message=message)


def archive_session(session_id: str, label: str) -> Outcome:
    """Toggle archive. `ok` means the write succeeded, not which direction it went."""
    now_archived = archive.toggle(session_id)
    if now_archived is None:
        return _failed(f"Could not record {label} as archived: ~/.ccw is not writable.", UNAVAILABLE)
    if now_archived:
        # Archive and keep are opposite answers to one question, so setting either clears the other. This module is the only place that knows, which is why neither store imports the other.
        keep.drop(session_id)
    return Outcome(ok=True, message=f"{label} archived." if now_archived else f"{label} is back in the list.")


def keep_session(session_id: str, label: str) -> Outcome:
    """Toggle keep. `ok` means the write succeeded, not which direction it went."""
    now_kept = keep.toggle(session_id)
    if now_kept is None:
        return _failed(f"Could not set {label} aside: ~/.ccw is not writable.", UNAVAILABLE)
    if now_kept:
        archive.forget_one(session_id)
    return Outcome(ok=True, message=f"{label} kept." if now_kept else f"{label} is no longer kept.")
