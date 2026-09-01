"""One session must read the same in the session list and in a restore plan.

The two surfaces resolve a display label from the same three inputs (hand-chosen agent name, transcript title, session id) and they used to do it with two separate implementations. This pins every branch of that policy across both, so a change to one that does not reach the other fails here rather than in the UI.
"""

from pathlib import Path
from typing import NamedTuple

import pytest

from claude_code_workspaces import restore, sessions, snapshot, workspaces
from conftest import conversation, live_session, write_transcript

# cwd name "api" slugged, plus two hex digits: the shape `claude agents` invents when nobody passed `--name`.
GENERATED = "api-eb"
HAND_CHOSEN = "retry-logic"
SESSION_ID = "11111111-2222-3333-4444-555555555555"
SHORT_ID = SESSION_ID[:8]


class Case(NamedTuple):
    """One branch of the policy: what was recorded, and what both surfaces must show for it."""

    agent_name: str | None
    title: str | None
    expected: str


CASES = [
    pytest.param(Case(HAND_CHOSEN, "Login bug", HAND_CHOSEN), id="hand-chosen name wins over the title"),
    pytest.param(Case(GENERATED, "Login bug", "Login bug"), id="a generated name loses to the title"),
    pytest.param(Case(GENERATED, None, GENERATED), id="a generated name is better than nothing"),
    pytest.param(Case(None, "Login bug", "Login bug"), id="title alone"),
    pytest.param(Case(None, None, SHORT_ID), id="neither, so the short id"),
]


@pytest.fixture
def project(home: Path) -> Path:
    cwd = home / "code" / "api"
    cwd.mkdir(parents=True)
    return cwd


@pytest.mark.parametrize("case", CASES)
def test_both_surfaces_agree_on_the_label(project: Path, home: Path, set_live, case: Case) -> None:
    write_transcript(home, SESSION_ID, project, conversation(SESSION_ID, project, title=case.title))
    running = live_session(SESSION_ID, project, name=case.agent_name)
    set_live(running)
    # The plan reads the name from a snapshot, which is where a crashed session's name survives.
    snapshot.take([running])

    rows, _ = sessions.collect()
    row = next(row for row in rows if row.session_id == SESSION_ID)

    plan = restore.build_plan()
    entry = next(entry for entry in plan.entries if entry.session_id == SESSION_ID)

    assert row.label == case.expected
    assert entry.label == row.label


def test_a_workspace_member_labels_the_same_way(project: Path, home: Path, set_live) -> None:
    """The workspace path stores the name at save time, so it resolves a label from a different source than the snapshot."""
    write_transcript(home, SESSION_ID, project, conversation(SESSION_ID, project, title="Login bug"))
    set_live(live_session(SESSION_ID, project, name=GENERATED))

    workspaces.save(workspaces.from_live("w", [live_session(SESSION_ID, project, name=GENERATED)]))
    plan = restore.plan_for_workspace(workspaces.load("w"))

    rows, _ = sessions.collect()
    row = next(row for row in rows if row.session_id == SESSION_ID)

    assert plan.entries[0].label == "Login bug"
    assert plan.entries[0].label == row.label
