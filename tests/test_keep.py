"""The keep store. `archive.py`'s shape, for the opposite intention."""

from pathlib import Path

from claude_code_workspaces import keep, paths


def test_keeping_a_session_records_it(home: Path) -> None:
    assert keep.toggle("s1") is True

    assert keep.kept() == {"s1"}


def test_keeping_the_same_session_twice_clears_it(home: Path) -> None:
    keep.toggle("s1")

    assert keep.toggle("s1") is False
    assert keep.kept() == set()


def test_dropping_a_mark_that_was_never_there_is_silent(home: Path) -> None:
    keep.drop("never-kept")

    assert keep.kept() == set()


def test_dropping_removes_only_the_one_named(home: Path) -> None:
    keep.toggle("s1")
    keep.toggle("s2")

    keep.drop("s1")

    assert keep.kept() == {"s2"}


def test_an_unreadable_store_reads_as_nothing_kept(home: Path) -> None:
    """Same rule as `archive.archived`: a broken file must not invent marks."""
    paths.kept_file().parent.mkdir(parents=True, exist_ok=True)
    paths.kept_file().write_text("{not json", encoding="utf-8")

    assert keep.kept() == set()


def test_entries_that_are_not_strings_are_dropped_rather_than_failing_the_read(home: Path) -> None:
    """A hand-edited file loses the bad entry, not the whole shelf."""
    paths.kept_file().parent.mkdir(parents=True, exist_ok=True)
    paths.kept_file().write_text('["s1", 7, ""]', encoding="utf-8")

    assert keep.kept() == {"s1"}


def test_a_failed_write_reports_rather_than_claiming_a_state(home: Path, monkeypatch) -> None:
    monkeypatch.setattr(keep.store, "write_json", lambda *_: False)

    assert keep.toggle("s1") is None
