"""The JSONL module's contract: read what it can, never raise, never let a caller see a half-truth."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_code_workspaces import store, transcripts
from claude_code_workspaces.paths import heads_cache
from conftest import conversation, write_transcript


def test_scan_reads_the_fields_the_lists_display(home: Path) -> None:
    cwd = home / "code" / "api"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd))

    session = transcripts.scan(path)

    assert session is not None
    assert session.session_id == "s1"
    assert session.title == "Login bug"
    assert session.first_prompt == "why is the login failing"
    assert session.cwd == cwd
    assert session.branch == "main"


def test_first_prompt_falls_back_to_content_blocks(home: Path) -> None:
    cwd = home / "code" / "app"
    records = conversation("s1", cwd, title=None)
    records[-2]["message"]["content"] = [{"type": "image"}, {"type": "text", "text": "  block form  "}]
    path = write_transcript(home, "s1", cwd, records)

    session = transcripts.scan(path)

    assert session is not None
    assert session.title is None
    assert session.first_prompt == "block form"


def test_a_block_without_usable_text_is_skipped_rather_than_guessed(home: Path) -> None:
    """The format is unstable, so a block whose `text` is not a string must not become `"None"` or crash."""
    cwd = home / "code" / "app"
    records = conversation("s1", cwd)
    records[-2]["message"]["content"] = [{"type": "text", "text": None}, {"type": "text"}]
    path = write_transcript(home, "s1", cwd, records)

    session = transcripts.scan(path)

    assert session is not None
    assert session.first_prompt is None


def test_tool_results_and_sidechains_are_not_the_users_first_prompt(home: Path) -> None:
    cwd = home / "code" / "app"
    records = conversation("s1", cwd)
    noise = [
        {"type": "user", "userType": "external", "isSidechain": True, "message": {"role": "user", "content": "subagent prompt"}},
        {"type": "user", "userType": "external", "isSidechain": False, "toolUseResult": {"stdout": "x"}, "message": {"role": "user", "content": "tool output"}},
    ]
    path = write_transcript(home, "s1", cwd, [*records[:2], *noise, *records[2:]])

    session = transcripts.scan(path)

    assert session is not None
    assert session.first_prompt == "why is the login failing"


def test_unparseable_lines_are_stepped_over(home: Path) -> None:
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd))
    path.write_text("{not json at all\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    session = transcripts.scan(path)

    assert session is not None
    assert session.title == "Login bug"


def test_a_transcript_of_pure_noise_still_produces_a_session(home: Path) -> None:
    """Degrading means listing the session with empty fields, not dropping it."""
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, [])
    path.write_text("garbage\ngarbage\n", encoding="utf-8")

    session = transcripts.scan(path)

    assert session is not None
    assert session.session_id == "s1"
    assert (session.title, session.first_prompt, session.cwd) == (None, None, None)


def test_the_head_limit_bounds_the_read(home: Path) -> None:
    cwd = home / "code" / "app"
    filler = [{"type": "system", "sessionId": "s1"} for _ in range(50)]
    records = filler + conversation("s1", cwd)
    path = write_transcript(home, "s1", cwd, records)

    truncated = transcripts.scan(path, head=10)
    complete = transcripts.scan(path, head=200)

    assert truncated is not None and truncated.title is None
    assert complete is not None and complete.title == "Login bug"


def test_nested_directories_are_not_sessions(home: Path) -> None:
    """Sub-agent transcripts live deeper than one level and must not be listed as sessions."""
    cwd = home / "code" / "app"
    write_transcript(home, "s1", cwd, conversation("s1", cwd))
    nested = home / ".claude" / "projects" / "some-tasks" / "child" / "deep"
    nested.mkdir(parents=True)
    (nested / "s2.jsonl").write_text("{}\n", encoding="utf-8")

    assert [session.session_id for session in transcripts.historical_sessions()] == ["s1"]


def test_a_projects_own_subagent_runs_are_not_sessions(home: Path) -> None:
    """The `*/*.jsonl` glob is one level deep on purpose, and nothing else fails if someone deepens it to `**/*.jsonl`.

    A project directory holds its sub-agent runs in `subagents/`, which are sub-agent transcripts rather than sessions. Measured on the reference machine 2026-09-01: 496 files at one level against 1765 recursive, so the deeper glob would inflate the session list by roughly 3.5x with rows nobody can resume.
    """
    cwd = home / "code" / "app"
    session = write_transcript(home, "s1", cwd, conversation("s1", cwd))
    subagents = session.parent / "subagents"
    subagents.mkdir()
    (subagents / "agent-abc.jsonl").write_text("\n".join(json.dumps(record) for record in conversation("agent-abc", cwd)) + "\n", encoding="utf-8")

    assert transcripts.transcript_paths() == [session]
    assert [item.session_id for item in transcripts.historical_sessions()] == ["s1"]


def test_transcript_path_finds_a_session_without_guessing_the_slug(home: Path) -> None:
    cwd = home / "code" / "acme.tools" / "DATA_X"
    expected = write_transcript(home, "s1", cwd, conversation("s1", cwd))

    assert transcripts.transcript_path("s1") == expected
    assert transcripts.transcript_path("nope") is None


def test_a_missing_projects_directory_is_reported_not_raised(home: Path) -> None:
    for child in (home / ".claude" / "projects").iterdir():
        child.unlink()
    (home / ".claude" / "projects").rmdir()

    assert transcripts.transcript_paths() == []
    assert transcripts.historical_sessions() == []


class _Unreadable:
    """`~/.claude/projects` as an antivirus lock, a permission blip or a roaming-profile hiccup leaves it: it is there, and it cannot be listed."""

    name = "projects"

    def glob(self, _pattern: str):
        raise PermissionError("access is denied")


def test_a_directory_that_cannot_be_listed_is_not_an_empty_directory(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The distinction the archive depends on: `[]` says nothing is there, `None` says nobody could look."""
    monkeypatch.setattr(transcripts, "claude_projects", _Unreadable)

    assert transcripts.transcript_paths() is None
    assert transcripts.historical_sessions() == []
    assert transcripts.scan_was_complete() is False
    assert transcripts.errors() and "PermissionError" in transcripts.errors()[0]


def test_a_completed_pass_says_so(home: Path) -> None:
    cwd = home / "code" / "app"
    write_transcript(home, "s1", cwd, conversation("s1", cwd))
    transcripts.historical_sessions()

    assert transcripts.scan_was_complete() is True


def test_errors_describe_the_pass_that_just_ran_not_the_worst_one_since_launch(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The count is shown as "N transcript(s) unreadable" beside a live list, so a failure that has stopped happening must stop being reported."""
    cwd = home / "code" / "app"
    write_transcript(home, "s1", cwd, conversation("s1", cwd))
    real = transcripts.claude_projects
    monkeypatch.setattr(transcripts, "claude_projects", _Unreadable)
    transcripts.historical_sessions()
    assert transcripts.errors() != []

    monkeypatch.setattr(transcripts, "claude_projects", real)
    transcripts.historical_sessions()

    assert transcripts.errors() == []


def test_last_activity_is_readable_even_when_the_contents_are_not(home: Path) -> None:
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, [])
    path.write_text("\x00\x00 not utf-8 json", encoding="utf-8")

    assert transcripts.last_activity(path) is not None


def test_json_records_survive_a_round_trip_through_the_scanner(home: Path) -> None:
    """Guards the assumption that a record is a dict: a bare array on a line must not crash the scan."""
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd))
    path.write_text(json.dumps([1, 2, 3]) + "\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    session = transcripts.scan(path)

    assert session is not None and session.title == "Login bug"


@pytest.mark.parametrize(
    ("poisoned", "expected"),
    [
        ("red \x1b[31mALERT\x1b[0m done", "red [31mALERT[0m done"),
        ("\x1b]0;pwned\x07title", "]0;pwnedtitle"),
        # No BEL: the sequence would otherwise stay open and swallow whatever the terminal printed after it.
        ("\x1b]0;pwned and the rest of the screen", "]0;pwned and the rest of the screen"),
        ("ding\x07dong", "dingdong"),
        ("two\tcolumns", "two columns"),
        ("first\nsecond", "first second"),
        ("wrapped\r\nline", "wrapped  line"),
        ("\x1b[2Jclear", "[2Jclear"),
    ],
)
def test_control_characters_never_leave_the_parser(home: Path, poisoned: str, expected: str) -> None:
    """A title or prompt holding an escape sequence is text to display, not instructions for the terminal."""
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd, prompt=poisoned, title=poisoned, branch=poisoned))

    session = transcripts.scan(path)

    assert session is not None
    assert session.title == expected
    assert session.first_prompt == expected
    assert session.branch == expected


def test_a_poisoned_block_form_prompt_is_cleaned_too(home: Path) -> None:
    cwd = home / "code" / "app"
    records = conversation("s1", cwd, title=None)
    records[-2]["message"]["content"] = [{"type": "text", "text": "\x1b[31mone"}, {"type": "text", "text": "two\x07"}]
    path = write_transcript(home, "s1", cwd, records)

    session = transcripts.scan(path)

    assert session is not None
    assert session.first_prompt == "[31mone two"


def test_text_that_only_looks_dangerous_survives_byte_for_byte(home: Path) -> None:
    """Turkish, other scripts and emoji are the normal case; a zero-width joiner is what a multi-person emoji is built from, so nothing outside the control range may be touched."""
    intact = "Giriş hatası çözüldü: ağ, İstanbul, ЖЖ, 日本語 👩‍💻🎉"  # noqa: RUF001 - the dotless i is the character under test, not a typo for an ASCII one
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd, prompt=intact, title=intact))

    session = transcripts.scan(path)

    assert session is not None
    assert session.title == intact
    assert session.first_prompt == intact


def test_a_head_cached_before_the_fix_is_cleaned_on_the_way_out(home: Path) -> None:
    """Sanitising on read is not enough on its own: the poisoned value is already in `heads.json`, and that entry is served without the transcript ever being opened again."""
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd))
    info = path.stat()
    store.write_json(
        heads_cache(),
        {
            "version": transcripts.HEADS_CACHE_VERSION,
            "heads": {
                "s1": {
                    "size": info.st_size,
                    "modified": datetime.fromtimestamp(info.st_mtime, tz=UTC).isoformat(),
                    "cwd": str(cwd),
                    "title": "\x1b]0;pwned\x07cached title",
                    "firstPrompt": "cached\x1b[31m prompt",
                    "branch": "ma\x07in",
                },
            },
        },
    )

    sessions = transcripts.historical_sessions()

    assert [session.title for session in sessions] == ["]0;pwnedcached title"]
    assert [session.first_prompt for session in sessions] == ["cached[31m prompt"]
    assert [session.branch for session in sessions] == ["main"]


def test_a_cached_entry_of_the_wrong_type_reads_as_absent(home: Path) -> None:
    """`heads.json` is loosely typed by the time it comes back off disk, and cleaning it must not turn a number into the string `"7"`."""
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd))
    info = path.stat()
    store.write_json(
        heads_cache(),
        {
            "version": transcripts.HEADS_CACHE_VERSION,
            "heads": {"s1": {"size": info.st_size, "modified": datetime.fromtimestamp(info.st_mtime, tz=UTC).isoformat(), "cwd": str(cwd), "title": 7, "firstPrompt": ["nope"], "branch": None}},
        },
    )

    sessions = transcripts.historical_sessions()

    assert [(session.title, session.first_prompt, session.branch) for session in sessions] == [(None, None, None)]


def test_a_title_of_nothing_but_control_characters_is_no_title(home: Path) -> None:
    cwd = home / "code" / "app"
    path = write_transcript(home, "s1", cwd, conversation("s1", cwd, title="\x1b\x07\t"))

    session = transcripts.scan(path)

    assert session is not None and session.title is None
