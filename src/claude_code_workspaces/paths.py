"""Filesystem locations. `claude_config` / `claude_projects` are read-only; writes go under `ccw_home`."""

from pathlib import Path


def claude_home() -> Path:
    return Path.home() / ".claude"


def claude_projects() -> Path:
    return claude_home() / "projects"


def claude_config() -> Path:
    return Path.home() / ".claude.json"


def ccw_home() -> Path:
    return Path.home() / ".ccw"


def workspaces_dir() -> Path:
    return ccw_home() / "workspaces"


def snapshots_dir() -> Path:
    return ccw_home() / "snapshots"


def trash_dir() -> Path:
    return ccw_home() / "trash"


def archive_file() -> Path:
    return ccw_home() / "archived.json"


def kept_file() -> Path:
    return ccw_home() / "kept.json"


def roots_cache() -> Path:
    return ccw_home() / "roots.json"


def heads_cache() -> Path:
    """Parsed transcript heads. Holds conversation excerpts — the AI-generated title and the user's first message for every session — so `~/.ccw/` is not free of conversation content even though nothing under `~/.claude/` is touched."""
    return ccw_home() / "heads.json"
