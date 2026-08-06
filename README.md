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
- Start a new session or resume an existing one; replies stream in as the CLI
  completes each turn
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
| `q` | Quit (confirm with `Ctrl+Q`) |

Session titles and model choices are stored in `~/.codex-tui/overrides.json`;
the codex session files themselves are never modified. Deleted sessions are
moved to `~/.codex-tui/trash` instead of being erased.

## How it works

- Session transcripts are parsed from the codex JSONL files under
  `$CODEX_HOME/sessions/YYYY/MM/DD/`. Unknown or future event types are ignored
  so newer CLI versions don't break browsing.
- Sending a prompt runs
  `codex exec --json --skip-git-repo-check -s <sandbox> -C <project> "<prompt>"`
  for a new session, or
  `codex exec resume --json -s <sandbox> <session-id> "<prompt>"` to continue
  one. Output events are streamed into the chat, then the transcript is
  re-read from disk.
- Deleted sessions are moved to `~/.codex-tui/trash` instead of being erased.

## Limitations

- Turns are non-interactive (`codex exec --json`), so there are no in-TUI
  approval prompts: the `--sandbox` value controls what Codex may do.
  `workspace-write` allows file changes inside the selected project;
  `read-only` forbids writes; `danger-full-access` allows anything.
- If the `codex` binary is missing, sending a message shows an in-app error
  instead of crashing.
- Replies arrive per completed turn (the JSON exec mode emits whole
  `item.completed` messages, not character-by-character deltas).

## Development

```bash
uv run pytest -q
```
