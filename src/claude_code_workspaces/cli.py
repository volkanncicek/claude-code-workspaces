"""Typer entrypoint: named commands and, with no subcommand, the session-list TUI.

Crash-restore selection (checklist, `--json`) lives here; shared mutations and launcher preview/launch go through `service`.
"""

import json
import sys
from collections.abc import Iterable
from datetime import timedelta
from importlib.metadata import version as _version
from typing import NoReturn

import typer

from . import restore as restore_plan
from . import service, sessions, tui, workspaces
from .checklist import choose
from .restore import RestorePlan
from .service import Outcome

# Exit codes. 0 is success and 2 is Click's own usage error, so the failure classes start at 3. A caller branches on these; matching on the sentence is not a contract.
EXIT_REFUSED = 1  # The request was understood and refused: an unusable name, a name already taken, or a state that contradicts it.
EXIT_NOT_FOUND = 3  # Nothing exists under the name given: a workspace, a trashed workspace, or a session id.
EXIT_NOTHING_TO_DO = 4  # Nothing left to act on: an empty crash plan, or a selection where every session is already running or has lost its transcript.
EXIT_CANCELLED = 5  # The user said no, at the confirmation or in the checklist.
EXIT_NO_TTY = 6  # A confirmation was needed and there is no terminal to ask on. `--yes` is the way past it.
EXIT_UNAVAILABLE = 7  # The environment cannot do it: the launcher cannot be driven, the live set cannot be read, `~/.ccw` cannot be written.

_EXIT_FOR_KIND = {
    service.REFUSED: EXIT_REFUSED,
    service.NOT_FOUND: EXIT_NOT_FOUND,
    service.NOTHING: EXIT_NOTHING_TO_DO,
    service.UNAVAILABLE: EXIT_UNAVAILABLE,
}

app = typer.Typer(help="Save, restore and manage Claude Code sessions as workspaces. With no arguments `ccw` opens the session list, which is the way most of this is driven; the commands below are for scripts and for what the list has no room for.")


@app.callback(invoke_without_command=True)
def cli(
    ctx: typer.Context,
    show_version: bool = typer.Option(False, "--version", help="Show the installed version and exit."),
) -> None:
    if show_version:
        typer.echo(_version("claude-code-workspaces"))
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        tui.run()


def _fail(message: str, code: int, *, kind: str, details: Iterable[str] = (), as_json: bool = False) -> NoReturn:
    """The one way out of a failure: a JSON envelope for a machine, a sentence for a person, both on stderr, and a code the caller can branch on."""
    if _machine_readable(as_json):
        typer.echo(json.dumps({"error": {"code": kind, "message": message, "details": list(details), "exitCode": code}}, indent=1), err=True)
    else:
        typer.echo(message, err=True)
        for line in details:
            typer.echo(f"  {line}", err=True)
    raise typer.Exit(code=code)


def _report(outcome: Outcome, *, as_json: bool = False) -> None:
    """Progress and diagnostics, never data, so it writes only to stderr: a piped run must carry the JSON document on stdout and nothing else."""
    if not outcome.ok:
        _fail(outcome.message, _EXIT_FOR_KIND.get(outcome.kind, EXIT_REFUSED), kind=outcome.kind, details=outcome.lines, as_json=as_json)
    typer.echo(outcome.message, err=True)
    for line in outcome.lines:
        typer.echo(f"  {line}", err=True)


@app.command()
def restore(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the command that would be run and stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the checklist and restore the default selection."),
    as_json: bool = typer.Option(False, "--json", help="Print the plan as JSON. Implied when stdout is not a terminal."),
    limit: int = typer.Option(restore_plan.DEFAULT_PANE_CAP, "--limit", help="Maximum number of panes to open."),
    window_minutes: int = typer.Option(int(restore_plan.DEFAULT_WINDOW.total_seconds() // 60), "--window-minutes", help="How far back from the last recorded activity a transcript still counts as having been open."),
) -> None:
    """Crash restore from the last snapshot plus recent transcript mtimes. Always checklist-first (heuristic is fuzzy). A named workspace is restored from the session list `ccw` opens, not from here."""
    plan = restore_plan.build_plan(window=timedelta(minutes=window_minutes))

    if _machine_readable(as_json):
        typer.echo(json.dumps(_serialise(plan, limit), indent=1))

    # Checked after the plan is printed, so a machine still sees the empty entry list it is being told about, and before the early return, so the exit code does not depend on what stdout happens to be.
    if not plan.entries:
        _fail(restore_plan.EMPTY_CRASH_PLAN, EXIT_NOTHING_TO_DO, kind=service.NOTHING, as_json=as_json)

    if _machine_readable(as_json) and not yes:
        return  # The checklist needs a terminal. The plan is the answer; `--yes` is how a script acts on it.

    if yes:
        chosen = plan.default_selection(cap=limit)
        if not chosen:
            _fail("Nothing would be restored: every candidate is already running, missing, or came from the heuristic.", EXIT_NOTHING_TO_DO, kind=service.NOTHING, as_json=as_json)
    else:
        chosen = choose(plan, cap=limit)
        if chosen is None:
            _fail("Cancelled. Nothing was opened.", EXIT_CANCELLED, kind="cancelled", as_json=as_json)

    selected = {entry.session_id for entry in plan.entries if entry.restorable} & set(chosen)

    if dry_run:
        _report(service.preview_restore(plan, selected), as_json=as_json)
        return

    outcome = service.launch_restore(plan, selected)
    _report(outcome, as_json=as_json)
    if outcome.ok:
        service.record_restored_set()


@app.command()
def save(
    name: str = typer.Argument(..., help="What to call this set of conversations."),
    force: bool = typer.Option(False, "--force", help="Overwrite a workspace that already exists."),
) -> None:
    """Save the current live sessions under a fixed name. Use `ccw refresh` to update later."""
    _report(service.save_workspace(name, force=force))


@app.command(name="list")
def list_workspaces(
    as_json: bool = typer.Option(False, "--json", help="Print as JSON. Implied when stdout is not a terminal."),
) -> None:
    """Show every saved workspace."""
    found = workspaces.load_all()
    if _machine_readable(as_json):
        typer.echo(json.dumps([{"name": w.name, "updated": w.updated.isoformat(), "members": w.session_ids} for w in found], indent=1))
        return
    if not found:
        typer.echo("No workspaces yet. `ccw save <name>` records the sessions running now.", err=True)
        return
    for workspace in found:
        typer.echo(f"{workspace.name:<28.28} {len(workspace.members):>3} session(s)   updated {workspace.updated:%Y-%m-%d %H:%M} UTC")


@app.command()
def refresh(name: str = typer.Argument(..., help="The workspace to update.")) -> None:
    """Replace a workspace's contents with the sessions running right now."""
    _report(service.refresh_workspace(name))


@app.command()
def add(
    name: str = typer.Argument(..., help="The workspace to extend."),
    session_ids: list[str] = typer.Argument(..., help="Session ids to add. They may be running or finished."),
) -> None:
    """Add sessions to a workspace, whether or not they are still running."""
    rows, _ = sessions.collect()
    _report(service.add_sessions(name, session_ids, rows))


@app.command()
def remove(
    name: str = typer.Argument(..., help="The workspace to edit."),
    session_ids: list[str] = typer.Argument(..., help="Session ids to drop."),
) -> None:
    """Drop sessions from a workspace, whether or not they still exist."""
    _report(service.remove_sessions(name, session_ids))


@app.command()
def rename(
    old: str = typer.Argument(..., help="The workspace to rename."),
    new: str = typer.Argument(..., help="Its new name."),
) -> None:
    """Rename a workspace, keeping its contents and creation date."""
    _report(service.rename_workspace(old, new))


@app.command()
def rm(
    name: str = typer.Argument(..., help="The workspace to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Move a workspace to `~/.ccw/trash/`. Transcripts are never touched."""
    described = service.describe_workspace(name)
    if not described.ok:
        _report(described)
    if not yes:
        # No terminal means nobody to answer, and a prompt there would hang the caller rather than fail it.
        if _machine_readable(False):
            _fail(f"`ccw rm {name}` needs --yes when stdout is not a terminal: there is nobody to confirm the deletion with.", EXIT_NO_TTY, kind="no-tty")
        _report(described)
        if not typer.confirm("Move it to the trash?", err=True):
            _fail("Cancelled. Nothing was moved.", EXIT_CANCELLED, kind="cancelled")
    _report(service.delete_workspace(name))


@app.command()
def trash(
    as_json: bool = typer.Option(False, "--json", help="Print as JSON. Implied when stdout is not a terminal."),
) -> None:
    """Show deleted workspaces that can still be brought back."""
    found = workspaces.trashed()
    if _machine_readable(as_json):
        typer.echo(json.dumps([{"name": item.name, "deletedAt": item.deleted_at.isoformat(), "members": item.members} for item in found], indent=1))
        return
    if not found:
        typer.echo("The trash is empty.", err=True)
        return
    for item in found:
        typer.echo(f"{item.name:<28.28} {item.members:>3} session(s)   deleted {item.deleted_at:%Y-%m-%d %H:%M} UTC")
    typer.echo(f"`ccw untrash <name>` restores one. Only the {workspaces.KEEP_TRASHED} most recent deletions are kept.", err=True)


@app.command()
def untrash(name: str = typer.Argument(..., help="The deleted workspace to bring back.")) -> None:
    """Restore a deleted workspace from `~/.ccw/trash/`."""
    _report(service.undelete_workspace(name))


def _machine_readable(explicit: bool) -> bool:
    return explicit or not sys.stdout.isatty()


def _serialise(plan: RestorePlan, limit: int) -> dict:
    preselected = set(plan.default_selection(cap=limit))
    previewed = service.preview_restore(plan, preselected)
    return {
        "snapshotTakenAt": plan.snapshot_taken_at.isoformat() if plan.snapshot_taken_at else None,
        "notes": plan.notes,
        "cap": limit,
        # The runnable command line for `preselected`, which is the set `--yes` acts on. It is the one thing `--dry-run` is asked for, and the prose it travels with on a terminal goes to stderr, so without it here a machine reading stdout gets everything except the argv it asked to preview. Already quoted by `list2cmdline`; null when there is nothing to open or the launcher cannot be driven.
        "commandLine": previewed.lines[0] if previewed.ok and previewed.lines else None,
        "entries": [
            {
                "sessionId": entry.session_id,
                "label": entry.label,
                "cwd": str(entry.cwd),
                "root": str(entry.root),
                "source": entry.source,
                "lastActive": entry.last_active.isoformat() if entry.last_active else None,
                "live": entry.live,
                "missing": entry.missing,
                "restorable": entry.restorable,
                "trust": entry.trust.reason if entry.trust else None,
                "preselected": entry.session_id in preselected,
            }
            for entry in plan.entries
        ],
    }


def main() -> None:
    app()
