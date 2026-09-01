"""Whether a pane will show Claude's trust dialog.

Trust is keyed on the **git repository root** in `~/.claude.json` (`projects[<root>].hasTrustDialogAccepted`). That file is read, never written.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paths import claude_config
from .repos import git_root

TrustReason = Literal["trusted", "untrusted", "home", "unreadable"]


@dataclass(slots=True, frozen=True)
class TrustState:
    cwd: Path
    root: Path
    trusted: bool
    reason: TrustReason

    @property
    def prompts(self) -> bool:
        return not self.trusted


def _key(path: Path) -> str:
    """Normalize like `~/.claude.json`: forward slashes, casefold."""
    return str(path).replace("\\", "/").rstrip("/").casefold()


def trusted_roots() -> dict[str, bool] | None:
    """Root → trusted flag, or `None` if the config cannot be read."""
    try:
        with claude_config().open(encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    projects = config.get("projects")
    if not isinstance(projects, dict):
        return None
    return {_key(Path(name)): bool(entry.get("hasTrustDialogAccepted")) for name, entry in projects.items() if isinstance(entry, dict)}


def trust_for(cwd: Path, roots: dict[str, bool] | None) -> TrustState:
    root = git_root(cwd)
    if root == Path.home():
        # Home trust is session-only and never persisted, so `~` always prompts.
        return TrustState(cwd=cwd, root=root, trusted=False, reason="home")
    if roots is None:
        return TrustState(cwd=cwd, root=root, trusted=False, reason="unreadable")
    accepted = roots.get(_key(root), False)
    return TrustState(
        cwd=cwd,
        root=root,
        trusted=accepted,
        reason="trusted" if accepted else "untrusted",
    )
