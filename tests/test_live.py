"""The live source. Every failure mode has to name itself, because the whole tool degrades from here."""

import json
import subprocess
import threading
from pathlib import Path

import pytest

from claude_code_workspaces import live, shutdown

PAYLOAD = [
    {"pid": 26140, "cwd": r"C:\code\api", "kind": "interactive", "startedAt": 1785311562621, "sessionId": "s1", "name": "api-eb", "status": "busy"},
    {"pid": 20832, "cwd": r"C:\code\worker", "kind": "interactive", "startedAt": 1785311592036, "sessionId": "s2", "name": None, "status": "idle"},
]


class FakeProcess:
    """Stands in for the spawned `claude`. Only what `live.py` touches: it is spawned, communicated with, and can be killed."""

    def __init__(self, argv: list[str], stdout: str, stderr: str, returncode: int, hangs: bool, kwargs: dict) -> None:
        self.argv, self._stdout, self._stderr, self._hangs = argv, stdout, stderr, hangs
        self.kwargs = kwargs
        self.returncode = returncode
        self.killed = False

    def communicate(self, timeout: float | None = None):
        if self._hangs and not self.killed:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout or 0)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def fake_popen(stdout: str = "", stderr: str = "", returncode: int = 0, *, hangs: bool = False, seen: list | None = None):
    def popen(argv, **kwargs):
        process = FakeProcess(argv, stdout, stderr, returncode, hangs, kwargs)
        if seen is not None:
            seen.append(process)
        return process

    return popen


@pytest.fixture(autouse=True)
def claude_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.shutil, "which", lambda _name: "claude")
    shutdown.reset()  # a stop set by one case must not leak into the next


def test_the_documented_fields_are_carried_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(json.dumps(PAYLOAD)))

    sessions = live.live_sessions()

    assert [s.session_id for s in sessions] == ["s1", "s2"]
    assert sessions[0].name == "api-eb"
    assert sessions[0].status == "busy"
    assert sessions[0].cwd == Path(r"C:\code\api")
    assert sessions[1].name is None


def test_the_output_is_decoded_as_utf8_whatever_the_locale_codepage_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a style choice: on a Turkish Windows the locale codepage is cp1252, and it decodes a Turkish agent name into mojibake silently rather than raising."""
    spawned: list = []
    # The dotless i is the data under test, not a slip. RUF001 offers to replace it with an ASCII `i`, which would remove the only character that makes this a decoding test.
    payload = [{"pid": 1, "cwd": r"C:\code", "kind": "interactive", "startedAt": 1785311562621, "sessionId": "s1", "name": "Aile ağacı uygulaması", "status": "idle"}]  # noqa: RUF001
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(json.dumps(payload, ensure_ascii=False), seen=spawned))

    sessions = live.live_sessions()

    assert spawned[0].kwargs["encoding"] == "utf-8"
    assert sessions[0].name == "Aile ağacı uygulaması"  # noqa: RUF001


def test_started_at_is_read_as_epoch_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(json.dumps(PAYLOAD)))

    started = live.live_sessions()[0].started_at

    assert started.tzinfo is not None
    assert started.year == 2026


def test_entries_without_a_session_id_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(json.dumps([{"pid": 1}, *PAYLOAD])))

    assert len(live.live_sessions()) == 2


def test_a_missing_claude_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.shutil, "which", lambda _name: None)

    with pytest.raises(live.ClaudeUnavailable, match="PATH"):
        live.live_sessions()


def test_a_non_zero_exit_carries_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(stderr="not logged in", returncode=1))

    with pytest.raises(live.ClaudeUnavailable, match="not logged in"):
        live.live_sessions()


def test_output_that_is_not_json_is_an_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen("Welcome to Claude Code"))

    with pytest.raises(live.ClaudeUnavailable, match="did not return JSON"):
        live.live_sessions()


def test_json_that_is_not_an_array_is_an_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen('{"error": "nope"}'))

    with pytest.raises(live.ClaudeUnavailable, match="array"):
        live.live_sessions()


def test_a_hang_becomes_an_error_rather_than_a_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(hangs=True))

    with pytest.raises(live.ClaudeUnavailable, match="did not return"):
        live.live_sessions()


def test_a_session_waiting_on_a_prompt_keeps_the_status_the_list_paints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code 2.1.212 moved sandbox, MCP-input and managed-settings waits out of `busy` and widened `waitingFor`. Both are reported through the same lower-case `status`, and the extra field is not ours to read, so the whole change reaches us as more sessions saying `waiting`."""
    payload = [{"pid": 1, "cwd": r"C:\code\api", "kind": "interactive", "startedAt": 1785311562621, "sessionId": "s1", "name": "api-eb", "status": "waiting", "waitingFor": "sandbox request"}]
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen(json.dumps(payload)))

    assert live.live_sessions()[0].status == "waiting"


class TestAReadCanBeAbandoned:
    """Quitting used to wait for a `claude agents --json` nobody would use, which cost two seconds a quarter of the time."""

    def test_a_read_in_flight_is_killed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[FakeProcess] = []
        monkeypatch.setattr(live.subprocess, "Popen", fake_popen(hangs=True, seen=seen))

        def read():
            with pytest.raises(live.ClaudeUnavailable):
                live.live_sessions(timeout=0.05)

        worker = threading.Thread(target=read)
        worker.start()
        worker.join(timeout=5)

        shutdown.begin()
        live.abandon_in_flight()  # nothing left to kill, and it must not raise for that
        assert seen[0].argv[-2:] == ["agents", "--json"]

    def test_nothing_in_flight_is_not_an_error(self) -> None:
        shutdown.begin()
        live.abandon_in_flight()

    def test_a_stopped_reader_refuses_to_start_another(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Killing only what is already running left the worker free to spawn `claude` a moment later, and the wait came back in full."""
        spawned: list[FakeProcess] = []
        monkeypatch.setattr(live.subprocess, "Popen", fake_popen("[]", seen=spawned))
        shutdown.begin()
        live.abandon_in_flight()

        sessions, note = live.try_live_sessions()

        assert sessions is None
        assert "closing" in (note or "")
        assert spawned == []

    def test_a_finished_read_is_no_longer_tracked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Left in the set, every read of the session list would leak a handle for the life of the process."""
        monkeypatch.setattr(live.subprocess, "Popen", fake_popen("[]"))

        live.live_sessions()

        assert live._in_flight == set()
