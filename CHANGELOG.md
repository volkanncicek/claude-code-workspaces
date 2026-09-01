# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-02

First public release.

### Added

- **Session list TUI.** `ccw` with no arguments opens a searchable table of every Claude Code session found on disk, merging live status from `claude agents --json` with labels and content read from the transcripts under `~/.claude/projects/`. Sessions that are not currently running are listed alongside the ones that are; a status glyph carries the distinction.
- **Named workspaces.** `ccw save` records the sessions running right now under a name, and `ccw list`, `ccw refresh`, `ccw add`, `ccw remove` and `ccw rename` manage them afterwards. Membership is by session id, so a workspace survives the terminal that created it.
- **Restore into Windows Terminal.** Restoring a set of sessions reopens them as real tabs and panes, one tab per project and two panes per tab, each pane resuming the right conversation in the right working directory. A project with more sessions than fit spills into further tabs under the same title. A named workspace is restored from the session list `ccw` opens, which is the only surface that shows the names.
- **Crash restore.** `ccw restore` takes no name and no workspace: it rebuilds a candidate set from the most recent snapshot plus recent transcript modification times. Snapshots are taken opportunistically by any run that sees live sessions, so there is no daemon; a short history is kept so a crash cannot overwrite the set being recovered.
- **Checklist before anything opens.** A crash restore always shows what it is about to launch first. Entries are marked by where they came from (snapshot, or the fuzzy mtime heuristic, which starts unchecked), and each row flags a session that is already running, a transcript that has gone, or a pane that will hit Claude's trust dialog.
- **Trust preflight.** Trust is read from `~/.claude.json`, keyed on the git repository root rather than the raw working directory, so the checklist can say which panes will stop for a prompt before you open twenty of them.
- **Keep and archive.** Two opposite marks on a session: keep it so it is never lost in the noise, or archive it so it drops out of the list. Both are recorded under `~/.ccw/` by session id; nothing under `~/.claude/` is moved or deleted.
- **Recoverable deletes.** `ccw rm` moves a workspace to `~/.ccw/trash/`; `ccw trash` lists what is recoverable and `ccw untrash` brings one back. Transcripts are never touched.
- **`--json` output** on `ccw restore`, `ccw list` and `ccw trash`, implied whenever stdout is not a terminal, for driving the tool from a script or an agent. Failures come back as a structured error envelope on stderr, with a distinct exit code per failure class, so a caller branches on the code rather than matching on the sentence. `ccw list --json` says what a workspace holds, which is the part a script wanted a named restore for.

### Notes

- Pane launching is Windows Terminal specific and sits behind a `Launcher` interface; everything else is platform independent and the test suite runs on Linux as well as Windows.
- Spawned panes have the `CLAUDE_*` nested-session markers scrubbed and `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` set. Without that a restored session writes no transcript and never registers with `claude agents`.
- Requires Python 3.13 or later.

[Unreleased]: https://github.com/volkanncicek/claude-code-workspaces/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/volkanncicek/claude-code-workspaces/releases/tag/v0.1.0
