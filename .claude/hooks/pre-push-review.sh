#!/usr/bin/env bash
# Git pre-push hook: Claude Code reviews the commits about to be pushed, following REVIEW.md, on the claude.ai
# subscription the local `claude` command is signed in with. An Important finding stops the push.
#
# Install once: ln -sf ../../.claude/hooks/pre-push-review.sh .git/hooks/pre-push
# Skip once:    git push --no-verify
#
# Each review is saved under .git/claude-review/<commit>.md next to the diff it read; a commit that passed is not
# reviewed again.
set -euo pipefail

zero_sha=0000000000000000000000000000000000000000
repo_root=$(git rev-parse --show-toplevel)
review_dir="$(git rev-parse --git-dir)/claude-review"
mkdir -p "$review_dir"
cd "$repo_root"

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "claude-review: ANTHROPIC_API_KEY is set, so the review would be billed to the API, not the subscription." >&2
  echo "claude-review: unset it, or push with --no-verify." >&2
  exit 1
fi

while read -r local_ref local_sha remote_ref remote_sha; do
  # A deleted branch has nothing to review.
  [[ "$local_sha" == "$zero_sha" ]] && continue

  if [[ "$remote_sha" == "$zero_sha" ]]; then
    base=$(git merge-base origin/main "$local_sha")
  else
    base=$remote_sha
  fi
  [[ "$base" == "$local_sha" ]] && continue

  if [[ -f "$review_dir/$local_sha.pass" ]]; then
    echo "claude-review: $local_ref at ${local_sha:0:7} already passed review"
    continue
  fi

  echo "claude-review: reviewing $base..$local_sha ($local_ref -> $remote_ref) with Claude Code"
  review_file="$review_dir/$local_sha.md"
  diff_file="$review_dir/$local_sha.diff"
  {
    git log --format='commit %h%n%n%B' "$base..$local_sha"
    git diff "$base..$local_sha"
  } >"$diff_file"
  prompt="Review the change in $diff_file (its commit messages, then the diff of $base..$local_sha) in this \
repository; the working tree may differ, so trust the diff for what changed. Follow REVIEW.md exactly: what failure \
looks like here, file-and-line evidence, the Important / Nit / Pre-existing tags, at most 3 nits, and a summary that \
starts with the finding count. Read the surrounding code before reporting a finding. Review only; change nothing. End \
your answer with one last line that is exactly \`VERDICT: BLOCK\` if there is at least one Important finding or you \
could not review the change, otherwise exactly \`VERDICT: PASS\`."

  # The model is named because the user's default routes each turn to the cheapest model that can do it.
  if ! claude -p "$prompt" \
    --model claude-opus-5-5 \
    --permission-mode dontAsk \
    --allowedTools "Read" "Grep" "Glob" \
    --no-session-persistence \
    >"$review_file"; then
    echo "claude-review: the claude command failed; its output is in $review_file" >&2
    exit 1
  fi

  cat "$review_file"
  verdict=$(grep -E '^VERDICT: (PASS|BLOCK)$' "$review_file" | tail -n 1 || true)
  case "$verdict" in
    "VERDICT: PASS")
      touch "$review_dir/$local_sha.pass"
      ;;
    "VERDICT: BLOCK")
      echo "claude-review: push stopped; the review is in $review_file. Fix what it found or push with --no-verify" >&2
      exit 1
      ;;
    *)
      echo "claude-review: the review gave no VERDICT line; read $review_file" >&2
      exit 1
      ;;
  esac
done
