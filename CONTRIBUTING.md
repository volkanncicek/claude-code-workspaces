# Contributing

Thanks for looking. This is a one-person project, so the honest picture first: bug reports and small fixes are easy to say yes to, larger changes need a conversation before you spend an evening on them, and I review when I get a free evening rather than on any schedule. Nothing here is abandoned if it sits for a week.

For a clear bug fix, a typo, or anything obviously self-contained, just open a pull request. For a new feature or a change to how something behaves, open an issue first. Several things that look like gaps are deliberate, and a few need a measurement before anything is built, so a short exchange up front usually saves the larger half of the work.

## Getting set up

The project uses [uv](https://docs.astral.sh/uv/) and needs Python 3.13 or later.

```
uv sync
uv run ccw          # the TUI, from source
uv run ccw --help   # the CLI
```

Add dependencies with `uv add <package>` rather than editing pins by hand, so `uv.lock` stays in step.

## Running the checks

CI runs exactly these four, on Windows and on Ubuntu:

```
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

The suite takes a few minutes. It needs `git` on `PATH`, and that is all — you do not need Claude Code installed. The home directory is redirected to a temporary one, live session data is faked, and an autouse fixture refuses to let anything spawn a real terminal pane, so running the tests cannot touch your own sessions.

If you are on Linux or macOS, everything except pane launching still runs, which is the point of the next section.

## The contribution I would most like

**A tmux launcher.** Spawning panes is the only part of `ccw` that knows it is on Windows, and it already sits behind the `Launcher` protocol in `src/claude_code_workspaces/launcher.py`. An implementation of that protocol for tmux makes the whole tool work on Linux and macOS with nothing else changed. Everything outside that one file is platform independent on purpose, which is why CI runs the full suite on Ubuntu rather than skipping it there.

If you want to take it on, open an issue first so we can agree on how the launcher gets selected at runtime — that is the only design decision in it, and it is easier to settle before the code exists.

## Four things that break quietly

Most mistakes in this codebase announce themselves: a test fails, or ruff or ty complains. These four do not. Break one and the tests still pass while the tool silently corrupts the sessions it was supposed to restore. Each one is written into the docstring at the top of the module that owns it, and the short version is:

- **`launcher.py` — scrub the `CLAUDE_*` nested-session markers from a pane's environment and set `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`.** Skip it and the restored session writes no transcript and never registers with `claude agents`. The tool breaks what it just restored and cannot tell.
- **`trust.py` — resolve a working directory to its git root before any trust lookup.** Trust is keyed on the repository root, so comparing raw paths reports perfectly trusted directories as untrusted.
- **`transcripts.py` — this is the only module allowed to parse the JSONL transcripts.** The format is internal and changes between Claude Code releases. When it breaks, parsing must degrade to live data from `claude agents --json` and the tool must keep working, so nothing in there may raise.
- **`paths.py` — Claude Code's own data is read-only.** `~/.claude.json` and `~/.claude/projects/` are read, never written, moved or deleted. Everything `ccw` writes lives under `~/.ccw/`, and destructive actions on those files move to `~/.ccw/trash/` after a confirmation.

`CLAUDE.md` in the repository root states the same four for anyone working with an AI agent here.

Two smaller preferences, since a reviewer would otherwise raise them: prefer `claude agents --json` to anything derived from files, because it is the only supported interface for live session state; and no stub commands or placeholder scaffolding, a command exists once it does something.

## Opening a pull request

One change per pull request, and a description of what it does rather than which files it touches. Run the four checks locally first — a red CI run is usually the thing that stalls a review longest. If the change is user-visible, add a line under `## [Unreleased]` in `CHANGELOG.md`.

Expect review comments; getting them is normal and not a verdict on the work.
