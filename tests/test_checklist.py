"""The checklist. Nothing opens without it agreeing, so refusal is as important as confirmation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from textual.widgets import SelectionList

from claude_code_workspaces.checklist import _ChecklistApp, prompt_for
from claude_code_workspaces.restore import DEFAULT_PANE_CAP, RestoreEntry, RestorePlan, RestoreSource
from claude_code_workspaces.trust import TrustState


def entry(session_id: str, *, source: RestoreSource = "snapshot", live: bool = False, transcript: bool = True, trusted: bool = True, name: str | None = None, title: str | None = None) -> RestoreEntry:
    root = Path("C:/code/app")
    return RestoreEntry(
        session_id=session_id,
        cwd=root,
        root=root,
        agent_name=name,
        title=title,
        source=source,
        last_active=datetime.now(tz=UTC) - timedelta(minutes=5),
        trust=TrustState(cwd=root, root=root, trusted=trusted, reason="trusted" if trusted else "untrusted"),
        transcript=root / "t.jsonl" if transcript else None,
        live=live,
    )


def plan_of(*entries: RestoreEntry) -> RestorePlan:
    return RestorePlan(entries=list(entries), snapshot_taken_at=datetime.now(tz=UTC))


def checklist(plan: RestorePlan, *, cap: int = DEFAULT_PANE_CAP) -> _ChecklistApp:
    """The checklist with the small application `ccw restore` wraps it in. The session list pushes the same screen instead."""
    return _ChecklistApp(plan, cap=cap, title=None)


async def test_snapshot_entries_start_checked_and_heuristic_ones_do_not() -> None:
    plan = plan_of(entry("s1"), entry("s2", source="heuristic"))

    app = checklist(plan)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one(SelectionList).selected == ["s1"]


async def test_unrestorable_rows_are_shown_but_disabled() -> None:
    plan = plan_of(entry("s1"), entry("s2", live=True), entry("s3", transcript=False))

    app = checklist(plan)
    async with app.run_test() as pilot:
        await pilot.pause()
        selection_list = app.screen.query_one(SelectionList)
        assert selection_list.option_count == 3
        await pilot.press("a")
        assert selection_list.selected == ["s1"]


async def test_confirming_returns_the_checked_ids() -> None:
    app = checklist(plan_of(entry("s1"), entry("s2")))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")

    assert app.return_value is not None
    assert sorted(app.return_value) == ["s1", "s2"]


async def test_cancelling_returns_nothing_at_all() -> None:
    app = checklist(plan_of(entry("s1")))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")

    assert app.return_value is None


async def test_an_empty_selection_is_refused_rather_than_treated_as_cancel() -> None:
    app = checklist(plan_of(entry("s1")))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.press("r")
        assert app.is_running
        await pilot.press("escape")

    assert app.return_value is None


async def test_going_over_the_cap_is_refused_and_stays_editable() -> None:
    app = checklist(plan_of(entry("s1"), entry("s2"), entry("s3")), cap=2)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.press("r")
        assert app.is_running
        await pilot.press("n")
        await pilot.press("space")
        await pilot.press("r")

    assert app.return_value is not None
    assert len(app.return_value) == 1


async def test_the_expected_trust_prompts_are_stated_before_anything_opens() -> None:
    """The user is told once, not pane by pane."""
    app = checklist(plan_of(entry("s1", trusted=False)))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "trust prompt" in str(app.screen.query_one("#notes").render())


def test_a_row_carries_what_is_needed_to_decide() -> None:
    line = prompt_for(entry("s1", name="api-eb"))

    assert "api-eb" in line
    assert "app" in line
    assert "5m ago" in line


def test_a_row_says_why_it_cannot_be_opened() -> None:
    assert "already running" in prompt_for(entry("s1", live=True))
    assert "transcript gone" in prompt_for(entry("s1", transcript=False))
    assert "will prompt for trust" in prompt_for(entry("s1", trusted=False))


def test_a_row_without_a_name_falls_back_to_the_id() -> None:
    assert prompt_for(entry("abcdef123456")).strip().startswith("* abcdef12")
