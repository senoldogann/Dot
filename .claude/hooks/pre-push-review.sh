#!/usr/bin/env bash
# Git pre-push hook: Claude Code reviews the commits about to be pushed, following REVIEW.md, on the claude.ai
# subscription the local `claude` command is signed in with. An Important finding stops the push.
#
# Install once: ln -sf ../../.claude/hooks/pre-push-review.sh .git/hooks/pre-push
# Skip once:    git push --no-verify
#
# Each review is saved under .git/claude-review/<commit>.md next to the diff it read; a commit that passed is not
# reviewed again, and neither is a push that only brings main in (item 540): when the change the branch makes against
# main is the same patch as at the tip already on the remote, there is nothing of its own to read. After three earlier
# reviews of the branch that BLOCKed, the hook says so before the next one (REVIEW.md: reconsider the approach).
set -euo pipefail

# The change a tip makes against main as a patch id: line numbers and whitespace do not count, so lines of main moving
# under the change do not alter it, and a merge that resolved a conflict by editing the change does.
own_change() {
  local fork
  fork=$(git merge-base origin/main "$1" 2>/dev/null) || return 0
  git diff "$fork..$1" | git patch-id --stable | cut -d' ' -f1
}

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

  if [[ "$remote_sha" != "$zero_sha" ]] && git cat-file -e "${remote_sha}^{commit}" 2>/dev/null; then
    before=$(own_change "$remote_sha")
    after=$(own_change "$local_sha")
    if [[ -n "$after" && "$before" == "$after" ]]; then
      echo "claude-review: $local_ref at ${local_sha:0:7} only brings main in: the change is the patch that was at" \
        "${remote_sha:0:7}, reviewed then"
      touch "$review_dir/$local_sha.pass"
      continue
    fi
  fi

  blocked=0
  for earlier in "$review_dir"/*.md; do
    [[ -e "$earlier" ]] || continue
    commit=$(basename "$earlier" .md)
    if git merge-base --is-ancestor "$commit" "$local_sha" 2>/dev/null && grep -q '^VERDICT: BLOCK$' "$earlier"; then
      blocked=$((blocked + 1))
    fi
  done
  if ((blocked >= 3)); then
    echo "claude-review: $blocked earlier reviews of this branch BLOCKed. REVIEW.md: after 3 rounds stop and reconsider" \
      "the approach (the structural fix, or two smaller changes) rather than closing the next spelling." >&2
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
