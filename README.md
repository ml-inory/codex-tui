# codex-tui

[English](README.md) · [中文](README.zh-CN.md)

A Codex Desktop-like terminal UI built with [Textual](https://textual.textualize.io/),
backed by the locally installed `codex` CLI. The left sidebar manages projects
(working directories) and sessions; the right panel shows the selected
conversation and a prompt input.

## Features

- Sidebar with project list and per-project session list (read from
  `$CODEX_HOME/sessions`, e.g. `~/.codex/sessions`); projects show their
  deepest directory by default, `F2` toggles the full path (remembered);
  `Ctrl+B` hides the sidebar entirely for a full-width chat (remembered)
- `F1` opens a searchable keyboard shortcut reference, so the footer never
  needs to fit everything
- tmux-style split view: `Ctrl+\` opens a second read-only pane showing
  another session side by side (it even streams live), `Ctrl+T` swaps the
  active and watched session, and `Ctrl+\` again closes the split
- Markdown-rendered conversations (user messages as bubbles, assistant replies
  as markdown)
- Start a new session or resume an existing one; replies stream in
  token-by-token through a persistent `codex app-server` connection (the same
  protocol Codex Desktop uses), so it feels like the native CLI
- Jump between any sessions instantly with `Ctrl+O` (type to filter by title
  or project) or cycle through the current project's sessions with
  `Ctrl+Up` / `Ctrl+Down`; the sidebar keeps your current selection when the
  session list refreshes
- Turns run in the background: send a message in one session, switch to
  another and keep working. When a background session finishes, a toast
  appears, its title gets a `●` marker in the sidebar, and `Ctrl+G` jumps
  straight to the most recently finished session
- `Esc` interrupts the model mid-reply
- Mouse-drag to select chat text and `Ctrl+C` to copy it (OSC 52 clipboard,
  works over SSH in MobaXTerm / Windows Terminal); `Ctrl+Y` copies the last
  Codex reply and `Ctrl+Shift+Y` copies the whole conversation
- Fixed color scheme: the UI uses hard-coded dark-palette colors (the
  `textual-dark` look) and Textual is pinned to 8.x, so colors are identical
  on every machine and terminal regardless of its ANSI palette
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

### Install on another machine

`codex-tui` is a normal Python package, so any machine with `uv` can install it
as a global command. One-liner (Linux/macOS, including WSL):

```bash
curl -fsSL https://raw.githubusercontent.com/ml-inory/codex-tui/main/install.sh | bash
```

Or manually:

```bash
git clone https://github.com/ml-inory/codex-tui.git ~/codex-tui
uv tool install --editable ~/codex-tui
codex-tui
```

The script clones the repo to `~/codex-tui` (override with `--dir`, or use a
non-editable copy with `--release`), installs the `codex-tui` command into
uv's tool environment, and verifies it. Because the default install is
editable, `git -C ~/codex-tui pull` updates the app. The `codex` CLI must be
installed and logged in for turns to run; on Windows run the manual steps in
PowerShell instead.

Options:

| Option | Description |
| --- | --- |
| `--sandbox read-only\|workspace-write\|danger-full-access` | Sandbox passed to `codex exec`; default `workspace-write` (edits allowed inside the selected project). Overridable via `CODEX_TUI_SANDBOX`. |
| `--yolo` | Codex yolo mode: bypass all approval prompts and disable sandboxing (equivalent to `codex --dangerously-bypass-approvals-and-sandbox`). Forces the sandbox to `danger-full-access` and conflicts with an explicit `--sandbox` value. EXTREMELY DANGEROUS. |
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
| `Ctrl+D` | Delete the current session (press Ctrl+D again to confirm) |
| `Ctrl+R` | Rename the current session |
| `F1` | Open the keyboard shortcut reference (type to filter) |
| `F2` | Toggle project path display (deepest dir ↔ full path) |
| `F3` | Pick the model for the session (from `~/.codex/models.json`) |
| `F5` | Refresh projects / sessions |
| `F7` | Load earlier messages of the current conversation |
| `Ctrl+B` | Toggle the left sidebar (full-width chat when hidden) |
| `Ctrl+\` | Open/close the split pane (pick which session to watch) |
| `Ctrl+T` | Swap the active session with the watched one |
| `Ctrl+O` | Quick-switch to any session (type to filter, Enter to open) |
| `Ctrl+Up` / `Ctrl+Down` (or `Alt+Up` / `Alt+Down`) | Cycle through sessions of the current project |
| `Ctrl+G` | Jump to the most recently finished background session |
| `Ctrl+Y` | Copy the current session's last Codex reply |
| `Ctrl+Shift+Y` | Copy the whole current conversation (injected context excluded) |
| `Esc` | Interrupt the running turn |
| `q` | Quit (confirm with `Ctrl+Q`) |

## Slash commands

The prompt supports codex-style slash commands, handled locally instead of
being sent to the model:

| Command | Effect |
| --- | --- |
| `/clear`, `/new` | Clear the chat and start a fresh conversation (the old session stays in the sidebar) |
| `/help` | Open the keyboard shortcut reference |
| `/model` | Pick the session model |
| `/rename` | Rename the current session |
| `/quit`, `/exit` | Quit the TUI |

Unrecognized commands show an `unknown command` warning and are ignored.

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
- Several sessions can run turns at the same time; each one subscribes to its
  own thread's notifications, and streamed text is buffered per session so a
  background reply never leaks into the view you are currently looking at, and
  switching projects or sessions never stops a running turn.
  Tool activity is streamed live into the chat while a turn runs: every
  `exec_command` and file edit shows as a codex-style status line
  (`• Running <command>` → `• Ran <command>`, green on success / red on
  failure), and command output appears dimmed under the row as it is produced,
  so you can watch Codex work instead of waiting on a spinner. When the model
  is waiting on a background terminal, a blinking `• Waiting for background
  terminal · <command>` status line is shown, just like the codex CLI. Read-only
  exploration collapses into a colored `• Explored` cell (`└ Search <query> in
  <path>` / `Read <file>`, cyan action names), and file edits render as
  `• Edited <file> (+N -M)` with the diff preview colored green for added and
  red for removed lines, exactly like codex.
  While a turn runs, the chat status line shows an animated spinner
  (`⠋ Codex is working…`) and the session gets a spinning marker in the
  sidebar, so it is obvious the agent is working even from another view.
  Streamed text and tool output are coalesced into ~25 fps flushes (short
  replies still paint instantly), and long replies stream through bounded
  chunk widgets so each frame only re-renders a small tail instead of the
  whole message; the terminal title also shows the activity spinner while
  working. Assistant messages are rendered by a tiny built-in Markdown
  renderer (headings, bold/italic, inline code, fenced code blocks, lists,
  quotes) instead of a full CommonMark parser, so opening transcripts is
  ~10x faster.
  Switching sessions and projects is kept responsive too: session scanning
  runs off the UI thread, the sidebar list is rebuilt in one batch, and long
  transcripts are rendered lazily — only the visible tail is built when a
  session opens, older messages are prepended automatically as you scroll up,
  and `F7` jumps to the earliest message. Sidebar listing reads only session
  metadata (full transcripts are parsed on demand), so switching stays fast
  even with hundreds of large sessions.
  Completion of a background turn is reported with an in-app notification, a
  sidebar marker, and the `Ctrl+G` jump shortcut.
- If `codex app-server` is unavailable or a turn fails, the TUI falls back to
  `codex exec --json` automatically, and the transcript is re-read from disk.
- Deleted sessions are moved to `~/.codex-tui/trash` instead of being erased.

## Limitations

- Turns use the app-server's `approvalPolicy: "never"` (the same behaviour as
  non-interactive `codex exec`), so there are no in-TUI approval prompts: the
  `--sandbox` value controls what Codex may do.
  `workspace-write` allows file changes inside the selected project;
  `read-only` forbids writes; `danger-full-access` allows anything. `--yolo`
  is shorthand for the latter, with approvals already disabled.
- If the `codex` binary is missing, sending a message shows an in-app error
  instead of crashing.
- `codex app-server` is an experimental CLI surface; if a future codex release
  changes its protocol, the TUI degrades to `exec` mode instead of breaking.

## Development

```bash
uv run pytest -q
```
