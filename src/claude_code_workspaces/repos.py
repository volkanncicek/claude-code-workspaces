"""Repository root for a working directory.

`git` is the authority. Cached answers are re-checked against the disk before use; a failed check asks `git` again. Used by trust lookup, the Project column, and restore's one-tab-per-project grouping.
"""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import store
from .paths import roots_cache


def _ask_git(path: Path) -> Path | None:
    """Repository root, or `None` if not in a repo. Missing directories return `None` without spawning git."""
    if not path.is_dir():
        return None
    try:
        completed = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10.0, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.strip()
    return Path(root) if root else None


def _has_git_above(path: Path) -> bool:
    """Whether a `.git` exists at or above `path`. Only used to invalidate a cached "not a repo" answer — never to invent a root (that would bypass git and poison trust)."""
    return any((directory / ".git").exists() for directory in (path, *path.parents))


def _still_true(cwd: Path, root: Path | None) -> bool:
    """Whether a cached answer still matches the disk. A root dies if deleted or left; "not a repo" dies if `.git` appears above."""
    if root is None:
        return cwd.is_dir() and not _has_git_above(cwd)
    return (root / ".git").exists() and (cwd == root or root in cwd.parents)


class _Roots:
    """In-process and on-disk root cache; both drop answers via `_still_true`."""

    def __init__(self) -> None:
        self.resolved: dict[Path, Path] = {}
        self.learned: dict[str, str | None] = {}  # `None` means not in a repository.
        self.remembered: dict[str, str | None] | None = None
        # `git_roots` resolves across a thread pool, and every worker goes through `_load`. Without this each one finds `remembered` unset and reads the file for itself.
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str | None]:
        with self._lock:
            if self.remembered is None:
                payload = store.read_json(roots_cache())
                self.remembered = {key: value for key, value in payload.items() if isinstance(key, str) and (value is None or isinstance(value, str))} if isinstance(payload, dict) else {}
            return self.remembered

    def of(self, path: Path) -> Path:
        if (known := self.resolved.get(path)) is not None:
            return known
        remembered = self._load()
        if str(path) in remembered:
            recorded = remembered[str(path)]
            root = Path(recorded) if recorded is not None else None
            if _still_true(path, root):
                self.resolved[path] = root or path
                return self.resolved[path]
        found = _ask_git(path)
        # Gone directories are not cached — they say nothing about a later recreate.
        if path.is_dir():
            self.learned[str(path)] = str(found) if found is not None else None
        self.resolved[path] = found or path
        return self.resolved[path]

    def persist(self) -> None:
        """Write learned answers and drop stale ones, even on a run that learned nothing new."""
        remembered = self._load()
        keeping = {cwd: root for cwd, root in {**remembered, **self.learned}.items() if _still_true(Path(cwd), Path(root) if root is not None else None)}
        if keeping != remembered:
            store.write_json(roots_cache(), keeping)
            self.remembered = keeping

    def forget(self) -> None:
        self.resolved.clear()
        self.learned.clear()
        self.remembered = None


_roots = _Roots()


def git_root(path: Path) -> Path:
    """Repository root, or `path` itself when not in a repository."""
    return _roots.of(path)


def git_roots(paths: list[Path]) -> dict[Path, Path]:
    """Resolve many paths (parallel), then persist what was newly learned."""
    unique = list(dict.fromkeys(paths))
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(unique))) as pool:
        answers = dict(zip(unique, pool.map(git_root, unique), strict=True))
    _roots.persist()
    return answers


def forget_roots() -> None:
    """Clear the cache. For tests that rebuild repos under a fresh home."""
    _roots.forget()
