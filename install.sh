#!/usr/bin/env bash
#
# Install codex-tui on a fresh machine (Linux/macOS, including WSL).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ml-inory/codex-tui/main/install.sh | bash
#   bash install.sh [--dir DIR] [--release] [--branch BRANCH]
#
# Options:
#   --dir DIR        Clone destination (default: $HOME/codex-tui)
#   --release        Install a non-editable copy (default: editable, so
#                    `git pull` inside the clone updates the installed app)
#   --branch BRANCH  Branch to clone (default: main)
#
# Requirements: git, uv (https://docs.astral.sh/uv/), and the codex CLI
# (https://github.com/openai/codex). The codex CLI is only a warning here:
# the TUI starts anyway and shows an in-app error until codex is installed.

set -euo pipefail

REPO_URL="${CODEX_TUI_REPO_URL:-https://github.com/ml-inory/codex-tui.git}"
INSTALL_DIR="${CODEX_TUI_INSTALL_DIR:-$HOME/codex-tui}"
MODE="editable"
BRANCH="main"

usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --release)
            MODE="release"
            shift
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --help | -h)
            usage
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            ;;
    esac
done

fail_missing() {
    echo "missing required command: $1" >&2
    echo "  install it first: $2" >&2
    exit 1
}

command -v git >/dev/null 2>&1 || fail_missing "git" "https://git-scm.com/downloads"
command -v uv >/dev/null 2>&1 || fail_missing "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh"

if ! command -v codex >/dev/null 2>&1; then
    echo "warning: 'codex' CLI not found (https://github.com/openai/codex)" >&2
    echo "         the TUI will show an in-app error until codex is installed" >&2
fi

if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
    echo "error: $INSTALL_DIR exists but is not a git clone" >&2
    echo "       remove it or pass --dir with a different path" >&2
    exit 1
fi

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    echo "==> cloning $REPO_URL ($BRANCH) into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
else
    echo "==> updating existing clone at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
fi

if [[ "$MODE" == "editable" ]]; then
    echo "==> installing codex-tui (editable, tracks $INSTALL_DIR)"
    uv tool install --editable "$INSTALL_DIR"
else
    echo "==> installing codex-tui (standalone copy)"
    uv tool install "$INSTALL_DIR"
fi

if command -v codex-tui >/dev/null 2>&1; then
    echo
    echo "Done: $(command -v codex-tui)"
    codex-tui --help >/dev/null
    echo "Run 'codex-tui' from any directory. Update later with: git -C $INSTALL_DIR pull"
else
    echo "codex-tui installed but not found on PATH" >&2
    echo "add uv's bin directory (usually ~/.local/bin) to PATH and re-login" >&2
    exit 1
fi
