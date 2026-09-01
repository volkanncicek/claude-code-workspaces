"""The restore plan and how to build one.

Sources: a snapshot (default-ticked), the heuristic from recent transcript mtimes (unchecked), or a named workspace's members. Live sessions are listed but not restorable. Building a plan does not open anything — `launcher` and `service` do that.

`RestorePlan` answers questions about itself. Free functions here only *construct* plans; anything that reads or transitions one is a method, so a caller cannot reach a decision the plan does not know it made.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from . import live as live_source
from . import snapshot, transcripts
from .formatting import session_label
from .repos import git_root, git_roots
from .trust import TrustState, trust_for, trusted_roots
from .workspaces import Workspace

DEFAULT_WINDOW = timedelta(minutes=15)
DEFAULT_PANE_CAP = 16
# Snapshot older than this is offered unchecked — age is time since last `ccw` run, not since the crash.
TRUSTED_SNAPSHOT_AGE = timedelta(hours=12)

EMPTY_CRASH_PLAN = "Nothing to restore: no snapshot holds sessions and no transcript was written recently."

# "direct" is a single session opened straight from the session list, which belongs to no plan.
RestoreSource = Literal["snapshot", "heuristic", "workspace", "direct"]


@dataclass(slots=True, frozen=True)
class RestoreEntry:
    """One session a plan could reopen. Frozen: everything about it is decided at build time."""

    session_id: str
    cwd: Path
    root: Path
    agent_name: str | None  # Raw handle as recorded, same meaning as `LiveSession.name`.
    title: str | None  # Transcript ai-title.
    source: RestoreSource
    last_active: datetime | None
    trust: TrustState | None = None
    transcript: Path | None = None
    live: bool = False

    @property
    def missing(self) -> bool:
        """No transcript on disk, so there is nothing to resume."""
        return self.transcript is None

    @property
    def restorable(self) -> bool:
        return not self.missing and not self.live

    @property
    def label(self) -> str:
        return session_label(agent_name=self.agent_name, cwd=self.cwd, title=self.title, session_id=self.session_id)


@dataclass(slots=True)
class RestorePlan:
    entries: list[RestoreEntry] = field(default_factory=list)
    snapshot_taken_at: datetime | None = None
    snapshot_is_stale: bool = False
    # False when live source failed — unknown running set, not "nothing running".
    liveness_known: bool = True
    notes: list[str] = field(default_factory=list)

    def liveness_unknown(self, note: str) -> None:
        """Mark liveness unknown and refuse default ticks — without live data every session looks safe to reopen."""
        self.liveness_known = False
        self.notes.append(f"{note} Nothing is ticked: a session that is already running would open a second time.")

    @property
    def openable(self) -> list[RestoreEntry]:
        """Restorable and safe given known liveness. Empty when `liveness_known` is false (avoids double-opening)."""
        if not self.liveness_known:
            return []
        return [entry for entry in self.entries if entry.restorable]

    @property
    def dead(self) -> list[RestoreEntry]:
        """Entries whose transcript is gone. One name for the concept `RestoreEntry.missing` names per entry."""
        return [entry for entry in self.entries if entry.missing]

    def default_selection(self, *, cap: int = DEFAULT_PANE_CAP) -> list[str]:
        """Checklist preselection: openable snapshot/workspace entries up to `cap`. Nothing if liveness unknown. Heuristic never preselected. Stale snapshot → workspace only."""
        trusted = {"workspace"} if self.snapshot_is_stale else {"snapshot", "workspace"}
        return [entry.session_id for entry in self.openable if entry.source in trusted][:cap]

    def trust_prompts(self, session_ids: Iterable[str]) -> list[Path]:
        """Roots the chosen entries will be asked to trust, in plan order, once each."""
        selected = {entry.session_id for entry in self.entries} & set(session_ids)
        roots: list[Path] = []
        for entry in self.entries:
            if entry.session_id in selected and entry.trust and entry.trust.prompts and entry.root not in roots:
                roots.append(entry.root)
        return roots

    def panes_for(self, session_ids: Iterable[str]) -> dict[Path, list[RestoreEntry]]:
        """Chosen entries grouped by project root. How many tabs a group becomes is the launcher's call, not this one's."""
        wanted = set(session_ids)
        groups: dict[Path, list[RestoreEntry]] = {}
        for entry in self.entries:
            if entry.session_id in wanted:
                groups.setdefault(entry.root, []).append(entry)
        return groups


@dataclass(slots=True, frozen=True)
class _Candidate:
    """Snapshot entries, workspace members and bare heuristic ids reduced to the one shape `_materialise` needs. Keyed by session id by the caller, which is why the id is not a field."""

    source: RestoreSource
    cwd: Path | None
    name: str | None


def build_plan(*, window: timedelta = DEFAULT_WINDOW) -> RestorePlan:
    plan = RestorePlan()
    live, live_note = live_source.try_live_sessions()
    if live_note:
        plan.liveness_unknown(live_note)
    live_ids = {session.session_id for session in live or []}

    index = transcripts.paths_by_session_id()
    candidates: dict[str, _Candidate] = {}

    recorded = snapshot.latest_populated()
    if recorded is not None:
        plan.snapshot_taken_at, entries = recorded
        if datetime.now(tz=UTC) - plan.snapshot_taken_at > TRUSTED_SNAPSHOT_AGE:
            plan.snapshot_is_stale = True
            plan.notes.append(f"The last snapshot is from {plan.snapshot_taken_at:%Y-%m-%d %H:%M} UTC, so nothing is ticked for you. Check what you actually want back.")
        for entry in entries:
            cwd = entry.get("cwd")
            candidates[entry["sessionId"]] = _Candidate("snapshot", Path(cwd) if cwd else None, entry.get("name"))
    else:
        plan.notes.append("No snapshot yet: this plan comes from transcript modification times alone.")

    for session_id in _recently_written(index, window):
        candidates.setdefault(session_id, _Candidate("heuristic", None, None))

    return _materialise(plan, candidates, live_ids, index)


def plan_for_workspace(workspace: Workspace) -> RestorePlan:
    """Fixed member list resolved against current live/transcript state."""
    plan = RestorePlan()
    live, live_note = live_source.try_live_sessions()
    if live_note:
        plan.liveness_unknown(live_note)
    candidates = {member.session_id: _Candidate("workspace", member.cwd, member.name) for member in workspace.members}
    if not candidates:
        plan.notes.append(f"'{workspace.name}' holds no sessions. `ccw refresh {workspace.name}` fills it from what is live.")
    return _materialise(plan, candidates, {session.session_id for session in live or []}, transcripts.paths_by_session_id())


def _materialise(plan: RestorePlan, candidates: dict[str, _Candidate], live_ids: set[str], index: dict[str, Path]) -> RestorePlan:
    """Every candidate becomes an entry, live ones included: a plan lists what it found and marks a running session unrestorable, rather than hiding it and leaving the user wondering where it went."""
    roots = trusted_roots()
    if roots is None:
        plan.notes.append("~/.claude.json could not be read, so no pane can be checked for a trust prompt.")

    scans = {session_id: transcripts.scan(path) for session_id in candidates if (path := index.get(session_id))}
    git_roots([candidate.cwd for candidate in candidates.values() if candidate.cwd] + [scanned.cwd for scanned in scans.values() if scanned and scanned.cwd])

    for session_id, candidate in candidates.items():
        path = index.get(session_id)
        scanned = scans.get(session_id)
        cwd = candidate.cwd or (scanned.cwd if scanned else None) or Path.home()
        plan.entries.append(
            RestoreEntry(
                session_id=session_id,
                cwd=cwd,
                agent_name=candidate.name,
                title=scanned.title if scanned else None,
                root=git_root(cwd),
                source=candidate.source,
                last_active=scanned.modified if scanned else None,
                trust=trust_for(cwd, roots),
                transcript=path,
                live=session_id in live_ids,
            )
        )

    # Two passes rather than one key: the project ascends while its sessions descend, and a datetime cannot be negated to express that in a single tuple. The second sort is stable, so it keeps the order the first one established.
    plan.entries.sort(key=lambda entry: entry.last_active or datetime.min.replace(tzinfo=UTC), reverse=True)
    plan.entries.sort(key=lambda entry: str(entry.root).casefold())
    return plan


def _recently_written(index: dict[str, Path], window: timedelta) -> set[str]:
    stamped = {session_id: moment for session_id, path in index.items() if (moment := transcripts.last_activity(path)) is not None}
    if not stamped:
        return set()
    cutoff = max(stamped.values()) - window
    return {session_id for session_id, moment in stamped.items() if moment >= cutoff}
