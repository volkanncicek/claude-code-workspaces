"""Trust lookups. The false-untrusted bug is the reason this module exists, so it gets its own test."""

import json
from pathlib import Path

from claude_code_workspaces import trust
from conftest import make_repo, write_claude_config


def test_a_subdirectory_resolves_to_the_repository_root(home: Path) -> None:
    """The false alarm: five sessions were reported untrusted because trust is keyed on the root, not the cwd."""
    root = make_repo(home / "code" / "api")
    nested = root / "services"
    nested.mkdir()
    write_claude_config(home, {root: True})

    state = trust.trust_for(nested, trust.trusted_roots())

    assert state.root == root
    assert state.trusted
    assert not state.prompts


def test_a_directory_outside_any_repository_keys_on_itself(home: Path) -> None:
    plain = home / "notes"
    plain.mkdir()
    write_claude_config(home, {plain: True})

    state = trust.trust_for(plain, trust.trusted_roots())

    assert state.root == plain
    assert state.trusted


def test_an_unknown_root_prompts(home: Path) -> None:
    root = make_repo(home / "work" / "fresh")
    write_claude_config(home, {})

    state = trust.trust_for(root, trust.trusted_roots())

    assert state.reason == "untrusted"
    assert state.prompts


def test_home_always_prompts_because_its_trust_is_never_written(home: Path) -> None:
    write_claude_config(home, {home: True})

    state = trust.trust_for(home, trust.trusted_roots())

    assert state.reason == "home"
    assert state.prompts


def test_keys_are_matched_across_separator_and_case_differences(home: Path) -> None:
    """Claude Code writes forward slashes; Windows paths are case-insensitive."""
    root = make_repo(home / "work" / "MixedCase")
    config = home / ".claude.json"
    shouted = str(root).replace("\\", "/").upper()
    config.write_text(json.dumps({"projects": {shouted: {"hasTrustDialogAccepted": True}}}), encoding="utf-8")

    assert trust.trust_for(root, trust.trusted_roots()).trusted


def test_an_unreadable_config_is_a_stated_unknown_not_a_silent_pass(home: Path) -> None:
    (home / ".claude.json").write_text("{ broken", encoding="utf-8")

    assert trust.trusted_roots() is None
    state = trust.trust_for(home / "work", None)
    assert state.reason == "unreadable"
    assert state.prompts


def test_a_missing_config_is_handled_the_same_way(home: Path) -> None:
    assert trust.trusted_roots() is None


def test_a_config_without_a_projects_map_is_unreadable(home: Path) -> None:
    (home / ".claude.json").write_text('{"projects": []}', encoding="utf-8")

    assert trust.trusted_roots() is None
