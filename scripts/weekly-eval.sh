#!/bin/sh
# Play the eval catalog once and compare the score with the last saved report.
# Run it weekly from cron or a Dot schedule; it spends the model's tokens.
set -eu
cd "$(dirname "$0")/.."

home="${DOT_HOME:-$HOME/Library/Application Support/Dot}"
reports="$home/evals"
before="$(ls -t "$reports"/*.json 2>/dev/null | head -1 || true)"

uv run dotx eval run --repeat 1

after="$(ls -t "$reports"/*.json 2>/dev/null | head -1 || true)"
if [ -z "$after" ]; then
    echo "No eval report was written under $reports." >&2
    exit 1
fi
if [ -n "$before" ] && [ "$before" != "$after" ]; then
    uv run dotx eval report "$before" "$after"
else
    uv run dotx eval report "$after"
fi
