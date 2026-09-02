# claude-code-workspaces

A TUI and CLI that saves, restores and manages Claude Code sessions as named workspaces, on Windows Terminal.

Two records live under `docs/`, both gitignored and deliberately not published. `DESIGN.local.md` holds the decisions, their reasons and the measurements behind them; it is a record rather than forever canon, so when behaviour is in doubt prefer the non-negotiables below and the self-contained comments in the code itself. `BACKLOG.local.md` holds what is decided but not done, what is deliberately out of scope, and what is still an open question — **read it before proposing work**, because several things that look like gaps are deliberate and several others already carry the measurement that should decide them.

## Non-negotiables

These four silently corrupt behaviour rather than failing loudly.

1. **Scrub the nested-session markers when spawning a pane.** Remove `CLAUDE_CODE_CHILD_SESSION` and the other `CLAUDE_*` markers from the child environment, and set `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`. Without this, a restored session writes no transcript and never registers in `claude agents` — so the tool breaks the sessions it restored and is blind to it.

2. **`~/.claude.json` is read-only.** Trust state lives there but is internal, undocumented, and the file is rewritten continuously by running sessions. Read it, warn the user, never write it.

3. **Resolve a working directory to its git root before any trust lookup.** Trust is keyed on the repository root. Comparing raw paths produces false "untrusted" results.

4. **Confine JSONL transcript parsing to one module.** The format is documented as internal and unstable across releases. When it breaks, the tool must degrade to live data from `claude agents --json` and keep working.

## Commands

```powershell
uv run ccw                # run from source
uv run ccw --help
uv add <package>          # never hand-edit dependency pins
```

## Conventions

- The repository is public and entirely in English, including commits and comments. Chat may be in Turkish.
- Prefer `claude agents --json` over anything derived from files. It is the only officially supported interface for live session state.
- No stub commands or placeholder scaffolding. A command exists when it does something.
- **Claude Code's own data is never modified.** `~/.claude.json` and `~/.claude/projects/` are read, never written, moved or deleted. Everything `ccw` writes lives under `~/.ccw/`. A cluttered list is answered by archiving, which records an id and moves nothing.
- Destructive actions on `ccw`'s own files move to `~/.ccw/trash/` and are confirmed first.
- Platform-specific behaviour lives behind the `Launcher` interface. Everything else must stay platform independent.
- `NOTES.local.md` and any `*.local.md` are gitignored working notes. Do not read from or write to them as part of the build.
