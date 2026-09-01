"""What is remembered between runs, and when it has to be thrown away.

Both caches turn a first paint from 1.76s into 0.23s, and both would be silent corruption if their invalidation were wrong: a stale root is a wrong trust answer, a stale head is a session shown under the wrong name. So the rules are tested from the direction of going stale, not from the direction of being fast.
"""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from claude_code_workspaces import repos, store, transcripts
from conftest import conversation, make_repo, write_transcript


class TestRootsAreRememberedButVerified:
    def test_a_resolved_root_survives_into_the_next_run(self, home: Path) -> None:
        repo = make_repo(home / "code" / "app")
        nested = repo / "service"
        nested.mkdir()

        assert repos.git_roots([nested]) == {nested: repo}
        assert json.loads(repos.roots_cache().read_text(encoding="utf-8")) == {str(nested): str(repo)}

    def test_the_remembered_answer_is_used_without_asking_git(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = make_repo(home / "code" / "app")
        repos.git_roots([repo])
        repos.forget_roots()

        def forbidden(_path):
            raise AssertionError("a remembered root must not be re-resolved")

        monkeypatch.setattr(repos, "_ask_git", forbidden)

        assert repos.git_root(repo) == repo

    def test_a_repository_that_was_deleted_is_asked_again(self, home: Path) -> None:
        """The whole point of checking rather than trusting: git stays the authority."""
        repo = make_repo(home / "code" / "app")
        nested = repo / "service"
        nested.mkdir()
        repos.git_roots([nested])
        repos.forget_roots()
        shutil.rmtree(repo / ".git")

        assert repos.git_root(nested) == nested  # no longer in a repository, and it noticed

    def test_not_being_in_a_repository_is_remembered_too(self, home: Path) -> None:
        """8 of the reference machine's 30 directories exist and are not repositories, and asking `git` about each cost 0.18s of every launch."""
        plain = home / "notes"
        plain.mkdir()

        repos.git_roots([plain])

        assert store.read_json(repos.roots_cache()) == {str(plain): None}

    def test_a_remembered_negative_costs_no_subprocess(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        plain = home / "notes"
        plain.mkdir()
        repos.git_roots([plain])
        repos.forget_roots()

        def spawned(*_args, **_kwargs):
            raise AssertionError("git was asked again about a directory already known not to be a repository")

        monkeypatch.setattr(repos.subprocess, "run", spawned)

        assert repos.git_root(plain) == plain

    def test_git_init_after_the_fact_is_picked_up(self, home: Path) -> None:
        """The whole reason a negative is safe to keep: `git init` leaves a `.git`, and that is what the check looks for."""
        plain = home / "notes"
        plain.mkdir()
        assert repos.git_roots([plain]) == {plain: plain}
        repos.forget_roots()
        make_repo(plain)

        assert repos.git_roots([plain]) == {plain: plain}
        assert store.read_json(repos.roots_cache()) == {str(plain): str(plain)}

    def test_a_repository_created_above_it_is_picked_up(self, home: Path) -> None:
        """`git clone` and `git worktree add` land the `.git` above the directory, not in it."""
        nested = home / "work" / "notes"
        nested.mkdir(parents=True)
        assert repos.git_roots([nested]) == {nested: nested}
        repos.forget_roots()
        parent = make_repo(home / "work")

        assert repos.git_roots([nested]) == {nested: parent}

    def test_a_directory_that_is_gone_is_not_remembered_either_way(self, home: Path) -> None:
        """It says nothing about what will be there if it comes back."""
        repos.git_roots([home / "deleted"])

        assert store.read_json(repos.roots_cache()) in (None, {})

    def test_a_directory_that_is_gone_is_not_asked_about(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Transcripts outlive their folders. Spawning git for each cost 0.6s of every launch to be told what a stat settles."""

        def spawned(*_args, **_kwargs):
            raise AssertionError("git was spawned for a directory that does not exist")

        monkeypatch.setattr(repos.subprocess, "run", spawned)

        assert repos.git_root(home / "deleted" / "project") == home / "deleted" / "project"

    def test_the_cache_file_is_read_once_however_many_threads_want_it(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`git_roots` resolves across a pool of eight, and every worker asks the same lazy loader. Each one used to find it unset and read `roots.json` for itself."""
        reads = []
        real = store.read_json

        def counted(path: Path):
            if path == repos.roots_cache():
                reads.append(path)
                time.sleep(0.05)  # Widen the window the workers used to race through, so the miss is not a coin toss.
            return real(path)

        monkeypatch.setattr(repos.store, "read_json", counted)
        directories = [home / f"project-{index}" for index in range(8)]
        for directory in directories:
            directory.mkdir()

        repos.git_roots(directories)

        assert len(reads) == 1

    def test_a_root_whose_repository_vanished_is_dropped_from_the_file(self, home: Path) -> None:
        kept = make_repo(home / "code" / "kept")
        going = make_repo(home / "code" / "going")
        repos.git_roots([kept, going])
        repos.forget_roots()
        shutil.rmtree(going)

        repos.git_roots([kept])

        assert json.loads(repos.roots_cache().read_text(encoding="utf-8")) == {str(kept): str(kept)}


class TestHeadsAreRememberedButVerified:
    def test_a_scanned_head_is_reused_rather_than_read_again(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = home / "code" / "app"
        write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
        assert [session.title for session in transcripts.historical_sessions()] == ["Login bug"]

        def forbidden(*_args, **_kwargs):
            raise AssertionError("an unchanged transcript must not be scanned again")

        monkeypatch.setattr(transcripts, "scan", forbidden)
        again = transcripts.historical_sessions()

        assert [session.title for session in again] == ["Login bug"]
        assert again[0].cwd == root

    def test_a_transcript_that_grew_is_scanned_again(self, home: Path) -> None:
        """A session with no title yet may gain one two records later, and size is what shows that the head moved."""
        root = home / "code" / "app"
        path = write_transcript(home, "s1", root, conversation("s1", root, title=None))
        assert transcripts.historical_sessions()[0].title is None

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "ai-title", "aiTitle": "Named at last", "sessionId": "s1"}) + "\n")

        assert transcripts.historical_sessions()[0].title == "Named at last"

    def test_a_transcript_rewritten_to_the_same_length_is_scanned_again(self, home: Path) -> None:
        """Size alone cannot see a rewrite in place, and a title held on that evidence never comes back."""
        root = home / "code" / "app"
        path = write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
        assert transcripts.historical_sessions()[0].title == "Login bug"

        rewritten = path.read_text(encoding="utf-8").replace("Login bug", "Login BUG")
        path.write_text(rewritten, encoding="utf-8")
        later = time.time() + 60
        os.utime(path, (later, later))

        assert transcripts.historical_sessions()[0].title == "Login BUG"

    def test_the_modification_time_is_never_taken_from_the_cache(self, home: Path) -> None:
        """It moves on every append while the head does not, so it comes from the stat that validated the entry."""
        root = home / "code" / "app"
        path = write_transcript(home, "s1", root, conversation("s1", root))
        first = transcripts.historical_sessions()[0].modified

        later = time.time() + 60
        os.utime(path, (later, later))

        assert transcripts.historical_sessions()[0].modified > first

    def test_a_trashed_transcript_leaves_the_cache(self, home: Path) -> None:
        root = home / "code" / "app"
        write_transcript(home, "kept", root, conversation("kept", root))
        going = write_transcript(home, "going", root, conversation("going", root))
        transcripts.historical_sessions()

        going.unlink()
        transcripts.historical_sessions()

        assert set(json.loads(transcripts.heads_cache().read_text(encoding="utf-8"))["heads"]) == {"kept"}

    def test_a_corrupt_cache_costs_a_rescan_and_nothing_else(self, home: Path) -> None:
        root = home / "code" / "app"
        write_transcript(home, "s1", root, conversation("s1", root, title="Login bug"))
        transcripts.heads_cache().parent.mkdir(parents=True, exist_ok=True)
        transcripts.heads_cache().write_text("{ truncated", encoding="utf-8")

        assert [session.title for session in transcripts.historical_sessions()] == ["Login bug"]


class TestAtomicWrites:
    def test_nothing_is_left_behind_when_the_payload_cannot_be_written(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = home / ".ccw" / "thing.json"
        target.parent.mkdir(parents=True, exist_ok=True)

        def fails(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(store.json, "dump", fails)

        assert store.write_json(target, {"a": 1}) is False
        assert not target.exists()
        assert list(target.parent.glob("*.tmp")) == []

    def test_an_existing_file_survives_a_failed_write(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The reason writes are staged: a crash mid-write must not destroy what was already there."""
        target = home / ".ccw" / "thing.json"
        store.write_json(target, {"good": True})

        monkeypatch.setattr(store.json, "dump", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
        store.write_json(target, {"bad": True})

        assert store.read_json(target) == {"good": True}

    def test_a_payload_that_cannot_be_serialised_is_a_failed_write_not_an_exception(self, home: Path) -> None:
        """`write_json` reports success as a boolean, so a `TypeError` out of `json.dump` would break the contract and strand the staged file in `~/.ccw/`."""
        target = home / ".ccw" / "thing.json"

        assert store.write_json(target, {"session": object()}) is False
        assert not target.exists()
        assert list(target.parent.glob("*.tmp")) == []

    def test_an_unreadable_file_reads_as_nothing(self, home: Path) -> None:
        missing = home / ".ccw" / "absent.json"

        assert store.read_json(missing) is None
