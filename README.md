# codex-tui

A Codex Desktop-like terminal UI built with [Textual](https://textual.textualize.io/),
backed by the locally installed `codex` CLI. The left sidebar manages projects
(working directories) and sessions; the right panel shows the selected
conversation and a prompt input.

## Features

- Sidebar with project list and per-project session list (read from
  `$CODEX_HOME/sessions`, e.g. `~/.codex/sessions`)
- Markdown-rendered conversations (user messages as bubbles, assistant replies
  as markdown)
- Start a new session or resume an existing one; replies stream in
  token-by-token through a persistent `codex app-server` connection (the same
  protocol Codex Desktop uses), so it feels like the native CLI
- Jump between any sessions instantly with `Ctrl+O` (type to filter by title
  or project) or cycle through the current project's sessions with
  `Ctrl+Up` / `Ctrl+Down`; the sidebar keeps your current selection when the
  session list refreshes
- `Esc` interrupts the model mid-reply
- Delete sessions (moved to `~/.codex-tui/trash`, recoverable)
- Configurable sandbox so Codex can actually edit project files

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/) (or `pip`)
- The `codex` CLI (`codex --version`), logged in / configured — the TUI reuses
  your existing `~/.codex` auth and model config, including custom providers

## Usage

```bash
uv run codex-tui
```

Or install it:

```bash
uv sync && uv run codex-tui
```

Options:

| Option | Description |
| --- | --- |
| `--sandbox read-only\|workspace-write\|danger-full-access` | Sandbox passed to `codex exec`; default `workspace-write` (edits allowed inside the selected project). Overridable via `CODEX_TUI_SANDBOX`. |
| `--codex-bin PATH` | Path to the codex binary (default: `codex`). |
| `--mode auto\|interactive\|exec` | Turn backend: `interactive` streams deltas via `codex app-server`; `exec` uses one-shot `codex exec --json`. Default `auto` (interactive, falling back to exec if the app-server is unavailable). Overridable via `CODEX_TUI_MODE`. |
| `--sessions-dir PATH` | Override the sessions directory (default: `$CODEX_HOME/sessions`). |
| `--clean-trash` | Permanently delete trashed session transcripts and exit. |

## Key bindings

| Key | Action |
| --- | --- |
| `Tab` / `Shift+Tab` | Move between sidebar and prompt |
| `Enter` | Send the prompt (new session if none is open, otherwise resume) |
| `Ctrl+N` | New session |
| `Ctrl+D` | Delete the current session (press again to confirm) |
| `Ctrl+R` | Rename the current session |
| `F3` | Pick the model for the session (from `~/.codex/models.json`) |
| `F5` | Refresh projects / sessions |
| `F7` | Load earlier messages of the current conversation |
| `Ctrl+O` | Quick-switch to any session (type to filter, Enter to open) |
| `Ctrl+Up` / `Ctrl+Down` (or `Alt+Up` / `Alt+Down`) | Cycle through sessions of the current project |
| `Esc` | Interrupt the running turn |
| `q` | Quit (confirm with `Ctrl+Q`) |

Session titles and model choices are stored in `~/.codex-tui/overrides.json`;
the codex session files themselves are never modified. Deleted sessions are
moved to `~/.codex-tui/trash` instead of being erased.

## How it works

- Session transcripts are parsed from the codex JSONL files under
  `$CODEX_HOME/sessions/YYYY/MM/DD/`. Unknown or future event types are ignored
  so newer CLI versions don't break browsing.
- In the default interactive mode a single `codex app-server --stdio` process
  stays alive for the whole TUI session. New conversations start with
  `thread/start`, existing ones resume with `thread/resume`, and every message
  is a `turn/start` on the live thread — no per-message CLI cold start. The
  server pushes `item/agentMessage/delta` notifications while the model
  streams, which the chat renders incrementally and converts to Markdown when
  the turn finishes.
- If `codex app-server` is unavailable or a turn fails, the TUI falls back to
  `codex exec --json` automatically, and the transcript is re-read from disk.
- Deleted sessions are moved to `~/.codex-tui/trash` instead of being erased.

## Limitations

- Turns use the app-server's `approvalPolicy: "never"` (the same behaviour as
  non-interactive `codex exec`), so there are no in-TUI approval prompts: the
  `--sandbox` value controls what Codex may do.
  `workspace-write` allows file changes inside the selected project;
  `read-only` forbids writes; `danger-full-access` allows anything.
- If the `codex` binary is missing, sending a message shows an in-app error
  instead of crashing.
- `codex app-server` is an experimental CLI surface; if a future codex release
  changes its protocol, the TUI degrades to `exec` mode instead of breaking.

## Development

```bash
uv run pytest -q
```
