# claude-code-workspaces

**A library for your Claude Code conversations: search them, keep them, group them into named workspaces, and open a whole set back into Windows Terminal tabs and split panes.**

Your Claude Code conversations pile up on disk — hundreds of them, keyed by UUID, spread across every project you have touched. `ccw` is the list you never had: search them, set one aside, name a set of them, and open that set back into real tabs and panes, each in the right directory, each resuming the right conversation.

It reads what is on disk, so it reaches every conversation you have ever had — including the ones that started on another machine, in another terminal, or months ago.

```
ccw            # interactive TUI — search, keep, and open a set
ccw list       # the workspaces you have named
ccw restore    # rebuild what was open before a crash or a reboot
```

## Before you install

- **Windows 10 or 11, with Windows Terminal.** Only the opening of panes is Windows-only, and [Platform support](#platform-support) explains why that is the one part that is.
- **Python 3.13 or later.**
- **Claude Code**, recent enough for `claude agents --json`, which arrived in 2.1.145. The tool also needs the `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE` environment variable; that one is documented in Claude Code's environment-variable reference, but no release note says which version introduced it, so no minimum is claimed here. If your build is too old the symptom is specific: restored sessions do not show up in `claude agents` and write no transcript. If you see that, update Claude Code.

## Install

Either line works and both give you a `ccw` command. If [uv](https://docs.astral.sh/uv/) means nothing to you, take the second one: `pip` ships with Python.

```powershell
uv tool install claude-code-workspaces
pip install claude-code-workspaces
```

With uv you can also run it once without installing anything. The package and the command have different names, so both have to be named:

```powershell
uvx --from claude-code-workspaces ccw
```

## First run

Type `ccw`. It reads what is already on your disk and opens a table of your conversations — nothing is written and no terminal opens until you ask for one.

Move with the arrow keys, `/` to search, `q` to quit. The keys that do something to the row you are on are `o` to resume it, `f` to fork it, `k` to keep it at the top of the list, `a` to archive it out of the way, and `s` to save the sessions running right now as a named workspace. `w` opens those saved workspaces, and that is where you reopen a whole set. The full list is along the bottom of the screen, so there is nothing to memorise.

## What it does

- **Every conversation, not just the live ones** — the list is built from `~/.claude/projects`, so it reaches sessions that finished, moved machine, or never ran under any session manager
- **Workspaces** — name a set of sessions ("api-refactor") and open it whenever you want, across projects, into one tab layout
- **Keep** — `k` sets a conversation aside with no typing; kept ones sit above the rest of the list until you come back to them
- **Live status** — see which sessions are busy, idle, or *waiting on you*, taken from Claude Code's own `claude agents --json`
- **Crash recovery** — `ccw restore` rebuilds what was open, from the last snapshot plus a modification-time heuristic, behind a checklist, with no daemon and no background service
- **Windows Terminal native** — real tabs and split panes via `wt`, no WSL and no multiplexer in between

## Why another one

This is a crowded field, and the honest answer has three parts.

### Claude Code already does a lot of this

If you are on a current CLI, `claude agents` is a session browser that ships with the product, and much of the "list my past conversations" story is now native. `/resume` in the agent view opens a picker of past sessions and resumes your pick (2.1.212); the picker defaults to the current directory, with `Ctrl+A` to widen it to all projects (2.1.108). `/fork` copies a conversation into a new session of its own (2.1.212). Sessions can be pinned so they stay alive when idle (2.1.147). A `Notification` hook fires when a session needs input or finishes (2.1.198). And since 2.1.248 opening a session you already resumed in another terminal no longer starts a second process on that conversation — the same guard `ccw` implements as `RestorePlan.openable`.

**So use the native picker for one session.** What it does not do is a *set*: its restore target is a background session in `claude agents`, not a pane in the terminal you are sitting in, and it opens one conversation at a time. `ccw` exists for the case where the unit is four conversations, in three directories, arranged in tabs.

### The wider field

Several good tools read the same transcript store, so "it reads what is on disk" no longer separates anything on its own:

| Project | What it is |
|---|---|
| [ccmanager](https://github.com/kbwo/ccmanager) (1.2k★) | A TUI that manages sessions across eight agent CLIs, without needing tmux |
| [claude-squad](https://github.com/smtg-ai/claude-squad) (8.4k★) | Multiple agents in tmux, each in its own git worktree |
| [claude-history](https://github.com/raine/claude-history) (469★) | Fuzzy search across your transcripts, then resume or fork the hit |
| [claude-code-log](https://github.com/daaain/claude-code-log) (1.2k★) | Turns transcripts into readable HTML |
| [wt-restore-claude-tabs](https://github.com/andrelsjunior/wt-restore-claude-tabs) | Rebuilds Windows Terminal tabs from the same transcripts after a crash, from one bash file under WSL. Tabs, not panes, chosen by a time window |
| [herdr](https://github.com/herdrdev/herdr) (34k★) | An agent runtime: a background server holds the PTYs, so panes and the agents inside them outlive the client. Native Windows since v0.8.2 |

Star counts read 2026-09-01.

herdr deserves the specific mention, because it answers the crash case *better* than any restore tool does — it makes the loss rare instead of cheap. **If losing panes to a crash is your whole problem, install a runtime; this README will not pretend otherwise.** The difference that remains is what the two can see: a runtime knows the panes it started, while `ccw` reads every transcript on disk, which is why it can reach a conversation that no session manager was running for.

### So when is `ccw` the wrong choice?

- **You want one conversation back.** Use `/resume`, or `claude --resume <id>`. That is one keystroke against installing a tool.
- **You want to search what was said inside conversations.** `ccw` filters on title, path, branch and first message. Use `claude-history`, or `wt-restore-claude-tabs --grep`.
- **You want your panes never to die.** Use a runtime like herdr. Recovering afterwards is a worse answer than not losing it.
- **You are on macOS or Linux.** Only the Windows Terminal launcher is written; see below.
- **You want worktrees, cost dashboards, or several agent CLIs in one view.** Those are `claude-squad` and `ccmanager`, and are deliberately out of scope here.

What is left, and what this tool is actually for: **named, curated, cross-project sets of past conversations, restored into real Windows Terminal tabs and split panes.** Nothing above does the set.

## Platform support

The core — reading sessions, live status, workspaces — is platform independent, because it builds on `claude agents --json` and `~/.claude/projects`, which are identical everywhere. Only *launching* panes is platform specific, and that lives behind a single `Launcher` interface.

Windows Terminal ships first because it is what the author runs and the only launcher this machine can honestly test. A tmux launcher is planned; contributions for other terminals are welcome.

## Development

```powershell
git clone https://github.com/volkanncicek/claude-code-workspaces
uv tool install --editable ./claude-code-workspaces
```

`--editable` means `ccw` is on `PATH` in every shell and still runs the working tree, so a change to the source takes effect without reinstalling. Adding a dependency needs `uv tool install --editable . --force` to refresh the tool's own environment; changing code does not.

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the setup, the checks CI runs, and the conventions worth knowing before a pull request. [`SECURITY.md`](SECURITY.md) covers what the tool reads and writes, and how to report a vulnerability. [`CHANGELOG.md`](CHANGELOG.md) records what changed per release.

## Not affiliated with Anthropic

This is an independent project. It is not affiliated with, endorsed by, or sponsored by Anthropic. "Claude" and "Claude Code" are Anthropic's.

## License

MIT. See [LICENSE](LICENSE).
