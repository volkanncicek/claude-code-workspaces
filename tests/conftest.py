"""A throwaway home directory, so the tests exercise the real path logic.

`paths.py` derives everything from `Path.home()`, which on Windows reads `USERPROFILE` and elsewhere reads `HOME`. Redirecting those gives the whole tool a fake `~/.claude`, `~/.claude.json` and `~/.ccw` without a single module needing to be monkeypatched or made injectable.
"""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_code_workspaces import launcher, live, repos, shutdown
from claude_code_workspaces.live import LiveSession


class _RefusesToSpawn:
    """`subprocess`, with `Popen` taken away. Everything else is passed straight through."""

    def __getattr__(self, attribute: str):
        return getattr(subprocess, attribute)

    @staticmethod
    def Popen(*args, **kwargs):  # Capitalised because it stands in for `subprocess.Popen`.
        raise AssertionError("a test reached the real launcher: stub `WindowsTerminalLauncher.launch`, or `launcher.subprocess.Popen` if the test is about the argv")


class _FakeTools:
    """`shutil`, with the tool lookup answered from a table. Everything else is passed straight through."""

    def __getattr__(self, attribute: str):
        return getattr(shutil, attribute)

    @staticmethod
    def which(name: str) -> str | None:  # Named for `shutil.which`, which it stands in for.
        return {"wt": r"C:\wt.exe", "pwsh": r"C:\pwsh.exe", "claude": r"C:\claude.exe"}.get(name)


@pytest.fixture(autouse=True)
def _no_real_panes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to spawn a real terminal, and answer the launcher's tool lookup from a table, from every test, without being asked.

    A safety rail rather than a convenience, and the only autouse fixture here. `subprocess.Popen` in `launcher` is the one call in the suite with an effect outside `tmp_path`: the argv is built before the fake home matters, so it opens Windows Terminal panes running `claude --resume` against the *real* `~/.claude`. A test that forgets to stub it does not fail — it silently starts conversations, which is exactly what one of these tests did while it was being written.

    The `shutil.which` half is the same rail pointed the other way: `WindowsTerminalLauncher.build` resolves `wt`, `pwsh` and `claude` on the host `PATH`, so without a table a test that reaches the real launcher passes on a machine with Claude Code and Windows Terminal installed and fails everywhere else with an `UNAVAILABLE` outcome instead of the line it asserted. Measured 2026-09-02 — the first CI run failed `test_a_dry_run_opens_nothing` on both runners for exactly that reason, and the fake paths are what its assertions now stand on.

    Only the launcher's own references are replaced, never `subprocess` or `shutil` themselves: `make_repo` and the live source spawn real processes on purpose, `subprocess.run` is built on `Popen`, and `live.py` does its own `which('claude')` that these tests answer separately. A test that means to inspect the argv, or to see a tool missing, stubs the attribute on the shim and its stub wins, because it is applied later.
    """
    monkeypatch.setattr(launcher, "subprocess", _RefusesToSpawn())
    monkeypatch.setattr(launcher, "shutil", _FakeTools())


@pytest.fixture
def set_live(monkeypatch: pytest.MonkeyPatch):
    """Decide what `claude agents --json` reports.

    Every module reads the live set through `live.try_live_sessions`, so patching it once covers the session list, restore, the service layer and both front ends. Defaults to nothing running; `unavailable=` is the separate case of not being able to tell.
    """

    def apply(*sessions: LiveSession, unavailable: str | None = None) -> None:
        answer = (None, unavailable) if unavailable else (list(sessions), None)
        monkeypatch.setattr(live, "try_live_sessions", lambda: answer)

    apply()
    return apply


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable in ("USERPROFILE", "HOME", "HOMEPATH", "HOMEDRIVE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path.home() == tmp_path, "the fake home did not take effect"
    # Roots are remembered in-process and on disk, which would otherwise leak resolutions between tests.
    repos.forget_roots()
    shutdown.reset()  # a previous test's app may have asked to stop on its way out
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    return tmp_path


def live_session(session_id: str, cwd: Path, *, name: str | None = None, status: str = "idle") -> LiveSession:
    """One entry as `claude agents --json` would report it."""
    return LiveSession(session_id=session_id, cwd=cwd, status=status, name=name, started_at=datetime.now(tz=UTC))


def write_transcript(home: Path, session_id: str, cwd: Path, records: list[dict]) -> Path:
    """Write a transcript where Claude Code would write it: one directory deep, named after the session."""
    slug = "".join(character if character.isalnum() else "-" for character in str(cwd))
    directory = home / ".claude" / "projects" / slug
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def conversation(session_id: str, cwd: Path, *, prompt: str = "why is the login failing", title: str | None = "Login bug", branch: str = "main") -> list[dict]:
    """The record shapes observed on the reference machine, trimmed to what the parser reads."""
    common = {"sessionId": session_id, "cwd": str(cwd), "gitBranch": branch, "version": "2.1.178", "isSidechain": False}
    records: list[dict] = [{"type": "last-prompt", "leafUuid": "abc", "sessionId": session_id}]
    if title is not None:
        records.append({"type": "ai-title", "aiTitle": title, "sessionId": session_id})
    records.append({**common, "type": "user", "userType": "external", "message": {"role": "user", "content": prompt}})
    records.append({**common, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "looking"}]}})
    return records


def write_claude_config(home: Path, projects: dict[Path, bool]) -> Path:
    """`~/.claude.json`, keyed the way Claude Code writes it: forward slashes."""
    path = home / ".claude.json"
    payload = {"projects": {str(root).replace("\\", "/"): {"hasTrustDialogAccepted": trusted} for root, trusted in projects.items()}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root
