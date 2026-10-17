#!/usr/bin/env bash
# Formats the file Claude Code just wrote or edited and lints it, as the gate would: Python with ruff, Rust with
# rustfmt. Lint errors left after ruff's fixes go back to Claude (exit 2) so it fixes them before going on.
set -euo pipefail

file_path=$(jq -r '.tool_input.file_path // empty')
[[ -n "$file_path" && -f "$file_path" ]] || exit 0

cd "$CLAUDE_PROJECT_DIR"
case "$file_path" in
  *.py)
    uv run --quiet ruff format --quiet "$file_path"
    if ! lint_output=$(uv run --quiet ruff check --fix --quiet "$file_path" 2>&1); then
      echo "$lint_output" >&2
      exit 2
    fi
    ;;
  *.rs)
    rustfmt --edition 2021 "$file_path"
    ;;
esac
