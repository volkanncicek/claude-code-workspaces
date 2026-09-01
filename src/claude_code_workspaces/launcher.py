"""Turn a restore plan into Windows Terminal panes. Platform-specific spawn lives behind `Launcher`.

Must scrub `CLAUDE_*` and set `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE` in each pane (nested-session markers disable persistence). Use an absolute `claude` path (`wt` panes get the system PATH). Layout is one tab per project, two panes to a tab; a project with more sessions than that gets further tabs under the same title.
"""

import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from .restore import RestoreEntry

CLAUDE_ENV_PREFIX = "CLAUDE_"

# Panes to a tab. Two side-by-side panes are still readable; a third makes every one of them too narrow to work in, so the project spills into another tab instead.
PANES_PER_TAB = 2

# An intention, not a terminal's spelling for it: each launcher maps these to its own flags.
Window = Literal["new", "current"]

NEW_WINDOW: Window = "new"
CURRENT_WINDOW: Window = "current"

# `wt -w last` is the most recently used window, which is the one the key was pressed in. Exhaustive on purpose: a conditional would map a future `Window` member to whichever branch it fell into, where this raises.
_WT_WINDOW: dict[Window, str] = {"new": "new", "current": "last"}


class LauncherUnavailable(RuntimeError):
    pass


class Launcher(Protocol):
    name: str

    def build(self, groups: dict[Path, list[RestoreEntry]], *, fork: bool = False, window: Window = NEW_WINDOW) -> list[str]: ...

    def launch(self, groups: dict[Path, list[RestoreEntry]], *, fork: bool = False, window: Window = NEW_WINDOW) -> None: ...


def _tab_title(root: Path) -> str:
    """Tab title with `wt`'s command separator removed.

    `wt` splits its command line on `;`, and Python quotes an argv element only when it holds whitespace, so a bare `;` arrives unquoted and starts a new subcommand. `;` is legal in a Windows directory name. A title is cosmetic, so dropping it is lossless; a path is not, which is why the cwd never goes through the argv at all.
    """
    return (root.name or str(root)).replace(";", "")


def _pane_script(entry: RestoreEntry, claude: str, *, fork: bool = False) -> str:
    """Pane PowerShell: scrub nested-session env here — `wt` panes inherit the parent terminal's environment.

    `-ErrorAction Stop` on the `Set-Location` is what keeps a session from resuming in the wrong project. The default `$ErrorActionPreference` is `Continue`, so a `Set-Location` to a directory that has since been deleted, renamed or unmounted prints an error and the script carries straight on, resuming that conversation in whatever directory the pane happened to start in — the tab title is right, the session really does resume, and Claude Code reads and writes files in the wrong repository. Terminating instead leaves the pane at a prompt (`pwsh -NoExit`) with PowerShell's own message, which already names the missing path and the command that could not reach it, so no message of ours is added on top of it.

    The check belongs here rather than in Python: a directory can disappear between planning a restore and launching it, so the only test that means anything is the one the pane runs at the moment it runs.
    """
    cwd = str(entry.cwd).replace("'", "''")
    session = entry.session_id.replace("'", "''")
    resume = f"& '{claude}' --resume '{session}'" + (" --fork-session" if fork else "")
    return "\n".join(
        [
            f"Get-ChildItem Env: | Where-Object {{ $_.Name -like '{CLAUDE_ENV_PREFIX}*' }} | ForEach-Object {{ Remove-Item -LiteralPath ('Env:\\' + $_.Name) -ErrorAction SilentlyContinue }}",
            "$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'",
            f"Set-Location -LiteralPath '{cwd}' -ErrorAction Stop",
            resume,
        ]
    )


def scrubbed_environment() -> dict[str, str]:
    """Parent env without `CLAUDE_*`, with force-persistence set — also applied to `wt.exe` itself."""
    env = {name: value for name, value in os.environ.items() if not name.startswith(CLAUDE_ENV_PREFIX)}
    env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    return env


class WindowsTerminalLauncher:
    name = "Windows Terminal"

    def build(self, groups: dict[Path, list[RestoreEntry]], *, fork: bool = False, window: Window = NEW_WINDOW) -> list[str]:
        terminal = shutil.which("wt")
        shell = shutil.which("pwsh")
        claude = shutil.which("claude")
        if terminal is None or shell is None or claude is None:
            missing = [
                name
                for name, found in (
                    ("wt", terminal),
                    ("pwsh", shell),
                    ("claude", claude),
                )
                if found is None
            ]
            raise LauncherUnavailable(f"Not on PATH: {', '.join(missing)}.")

        argv = [terminal, "-w", _WT_WINDOW[window]]
        opened = False
        for root, entries in groups.items():
            for index, entry in enumerate(entries):
                if opened:
                    argv.append(";")
                opened = True
                encoded = base64.b64encode(_pane_script(entry, claude, fork=fork).encode("utf-16-le")).decode("ascii")
                if index % PANES_PER_TAB == 0:
                    argv += ["new-tab", "--title", _tab_title(root)]
                else:
                    argv += ["split-pane", "-V"]
                # No `-d`: a `;` in the path cannot be quoted through `wt`'s parser (see `_tab_title`). The pane script sets the directory itself, escaped.
                # `-NoExit`: `-EncodedCommand` is a one-shot mode, so without it pwsh exits the moment `claude` does and the pane closes with it. Ending a session should leave a prompt to resume from, not take the pane away.
                argv += [
                    shell,
                    "-NoExit",
                    "-NoLogo",
                    "-NoProfile",
                    "-EncodedCommand",
                    encoded,
                ]
        return argv

    def launch(self, groups: dict[Path, list[RestoreEntry]], *, fork: bool = False, window: Window = NEW_WINDOW) -> None:
        if not any(entries for entries in groups.values()):
            return
        subprocess.Popen(self.build(groups, fork=fork, window=window), env=scrubbed_environment(), close_fds=True)


def default_launcher() -> Launcher:
    return WindowsTerminalLauncher()
