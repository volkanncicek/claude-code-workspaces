"""Display helpers shared by the checklist and the session list."""

import re
from datetime import UTC, datetime
from pathlib import Path


def is_generated_name(name: str | None, cwd: Path) -> bool:
    """True for the undocumented cwd-slug-plus-hex handles `claude agents` invents (not `claude --name`).

    Lives here rather than in `live` because deciding a handle is not worth showing is a display question, and `session_label` is its only caller.
    """
    if not name:
        return False
    slug = re.sub(r"[^a-z0-9]+", "-", cwd.name.lower()).strip("-")
    return re.fullmatch(rf"{re.escape(slug)}-[0-9a-f]{{2}}", name) is not None


def session_label(*, agent_name: str | None, cwd: Path, title: str | None, session_id: str) -> str:
    """The one label policy: a hand-chosen agent name, else the transcript title, else the generated handle, else a short id.

    Both surfaces call this, so a session reads the same in the list and in a restore plan. `tests/test_labels.py` pins that agreement.
    """
    if agent_name and not is_generated_name(agent_name, cwd):
        return agent_name
    return title or agent_name or session_id[:8]


def age(moment: datetime | None) -> str:
    if moment is None:
        return "never"
    seconds = (datetime.now(tz=UTC) - moment).total_seconds()
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f}h ago"
    return f"{seconds / 86400:.0f}d ago"


def clip(text: str, width: int) -> str:
    """Truncate with an ellipsis so a cut is visible."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"
