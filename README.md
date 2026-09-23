# claude-code-workspaces

**Search your Claude Code conversations, keep the ones that matter, group them into named workspaces, and reopen a whole set as Windows Terminal tabs and split panes.**

![The ccw session list in Windows Terminal: one row per conversation with its project, branch and when it was last seen, running sessions marked at the top](https://raw.githubusercontent.com/volkanncicek/claude-code-workspaces/main/docs/screenshot.png)

`ccw` reads every conversation Claude Code has saved on disk, including ones that finished months ago, started on another machine, or never ran under a session manager. Each one reopens in its own directory, resuming the right conversation.

```
ccw            # the TUI: search, keep, and open a set
ccw list       # your named workspaces
ccw restore    # reopen what was running before a crash or a reboot
```

## Requirements

Everything except opening panes works on Windows, macOS and Linux:

- **Python 3.13 or later.**
- **Claude Code 2.1.145 or later**, for `claude agents --json`. If restored sessions never appear in `claude agents` and write no transcript, your build predates `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE`: update Claude Code.

Opening panes needs one supported terminal:

- **Windows Terminal** on Windows 10 or 11, with PowerShell 7 or the Windows PowerShell 5.1 that ships with Windows. Nothing extra to install: panes use `pwsh` when it is there and `powershell` otherwise.

## Install

From [PyPI](https://pypi.org/project/claude-code-workspaces/), with either:

```powershell
uv tool install claude-code-workspaces
pip install claude-code-workspaces
```

Or run it once without installing: `uvx --from claude-code-workspaces ccw`.

## Usage

`ccw` opens a table of your conversations. Nothing is written and no terminal opens until you ask.

| Key | Does |
|---|---|
| `/` | search |
| `o` / `f` | resume / fork the conversation |
| `k` | keep it at the top of the list |
| `a` | archive it out of the way |
| `s` | save the running sessions as a named workspace |
| `w` | your workspaces, where a whole set reopens |
| `q` | quit |

Every key is listed along the bottom of the screen.

## What it does

- **Every conversation, not just the live ones**: the list comes from `~/.claude/projects`, not from a session manager.
- **Workspaces**: a named set of sessions across projects ("api-refactor"), reopened into one tab layout.
- **Live status**: busy, idle or *waiting on you*, from Claude Code's own `claude agents --json`.
- **Crash recovery**: `ccw restore` rebuilds what was open from the last snapshot and file modification times, behind a checklist. No daemon, no background service.
- **Native panes**: real Windows Terminal tabs and split panes, no WSL or multiplexer in between.

## When to use something else

Claude Code now handles a single session well: `claude agents` browses them, `/resume` picks one and resumes it, `/fork` copies one. `ccw` is for a *set*: four conversations in three directories, reopened as panes in the terminal you are sitting in.

### The wider field

Several good tools read the same transcript store:

| Project | What it is |
|---|---|
| [ccmanager](https://github.com/kbwo/ccmanager) | A TUI that manages sessions across eight agent CLIs, without needing tmux |
| [claude-squad](https://github.com/smtg-ai/claude-squad) | Multiple agents in tmux, each in its own git worktree |
| [claude-history](https://github.com/raine/claude-history) | Fuzzy search across your transcripts, then resume or fork the hit |
| [claude-code-log](https://github.com/daaain/claude-code-log) | Turns transcripts into readable HTML |
| [wt-restore-claude-tabs](https://github.com/andrelsjunior/wt-restore-claude-tabs) | Rebuilds Windows Terminal tabs from the same transcripts after a crash, from one bash file under WSL. Tabs, not panes, chosen by a time window |
| [herdr](https://github.com/herdrdev/herdr) | An agent runtime: a background server holds the PTYs, so panes and the agents inside them outlive the client |

herdr answers the crash case *better* than any restore tool, because it makes the loss rare instead of cheap. **If losing panes to a crash is your whole problem, install a runtime; this README will not pretend otherwise.**

### When to use something else

- **One conversation back**: `/resume`, or `claude --resume <id>`.
- **To search inside conversations**: `ccw` filters only on title, path, branch and first message. Use claude-history, or `--grep` in wt-restore-claude-tabs.
- **Panes that never die**: herdr. It knows only the panes it started, while `ccw` reads every transcript on disk.
- **Worktrees, cost dashboards, or several agent CLIs in one view**: claude-squad and ccmanager.
- **Panes on macOS or Linux**: not yet, see below.

## Platform support

The core builds on `claude agents --json` and `~/.claude/projects`, which are the same everywhere. Only opening panes is platform specific, and it lives behind the `Launcher` interface: on macOS and Linux everything else works, and the open key says it needs Windows Terminal.

Windows Terminal came first because it is the one launcher the author can test. A tmux launcher is planned, and other terminals are welcome: see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Development

```powershell
git clone https://github.com/volkanncicek/claude-code-workspaces
uv tool install --editable ./claude-code-workspaces
```

`ccw` then runs the working tree from any shell. After adding a dependency, run the install again with `--force`.

[`CONTRIBUTING.md`](CONTRIBUTING.md) covers setup, checks and conventions, [`SECURITY.md`](SECURITY.md) what the tool reads and writes, and [`CHANGELOG.md`](CHANGELOG.md) each release.

## Not affiliated with Anthropic

This is an independent project. It is not affiliated with, endorsed by, or sponsored by Anthropic. "Claude" and "Claude Code" are Anthropic's.

## License

MIT. See [LICENSE](LICENSE).
