#!/usr/bin/env bash
# Git pre-push hook: Dot reviews the commits about to be pushed, following REVIEW.md, in research mode (read-only), on
# the owner's Claude plan (`claude_code:opus`, effort medium: the owner's choice of 2026-11-20, after reviews on
# DeepSeek took 6 to 27 minutes and 3 to 14 million tokens). Its chat is kept under Dot's Code reviews (`--review`).
# An Important finding stops the push. Item 1211 moved the review off Claude Code once: that model answered `[cyber]`
# to a diff it declined to read; a review with no verdict is now done again on DeepSeek, and a second one with none
# stops the push.
#
# Install once: ln -sf ../../.claude/hooks/pre-push-review.sh .git/hooks/pre-push
# Skip once:    git push --no-verify
#
# Each review is saved under .git/claude-review/<commit>.md next to the diff it read; a commit that passed is not
# reviewed again, and neither is a push that only brings main in (item 540): when the change the branch makes against
# main is the same patch as at the tip already on the remote, which passed review here, there is nothing of its own to
# read. After three earlier
# reviews of the branch that BLOCKed, the hook says so before the next one (REVIEW.md: reconsider the approach).
set -euo pipefail

# The change a tip makes against main as a patch id: line numbers do not count, so lines of main moving under the change
# do not alter it, but whitespace does (--verbatim: a dedent changes what Python means), and so does a merge that
# resolved a conflict by editing the change. Nothing is printed when it cannot be told, and then the push is reviewed.
own_change() {
  local fork
  fork=$(git merge-base origin/main "$1" 2>/dev/null) || return 0
  git diff "$fork..$1" | git patch-id --verbatim 2>/dev/null | cut -d' ' -f1 || true
}

zero_sha=0000000000000000000000000000000000000000
repo_root=$(git rev-parse --show-toplevel)
# One folder for every worktree of the repository (item 1020), so that a tip that passed in one vouches in another.
review_dir="$(git rev-parse --path-format=absolute --git-common-dir)/claude-review"
mkdir -p "$review_dir"
cd "$repo_root"

while read -r local_ref local_sha remote_ref remote_sha; do
  # A deleted branch has nothing to review.
  [[ "$local_sha" == "$zero_sha" ]] && continue

  # A new branch, one rewritten by a rebase onto main (its old head is no ancestor of the new: item 955), and one that
  # took main in since its last push (where it leaves main moved: item 1020) are reviewed from where they leave main,
  # so that the pull requests main took meanwhile are not read as theirs.
  fork=$(git merge-base origin/main "$local_sha")
  if [[ "$remote_sha" == "$zero_sha" ]] || ! git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null ||
    [[ "$(git merge-base origin/main "$remote_sha" 2>/dev/null || true)" != "$fork" ]]; then
    base=$fork
  else
    base=$remote_sha
  fi
  [[ "$base" == "$local_sha" ]] && continue

  if [[ -f "$review_dir/$local_sha.pass" ]]; then
    echo "claude-review: $local_ref at ${local_sha:0:7} already passed review"
    continue
  fi

  # Only a remote tip that passed review here vouches for its change (item 1020): one pushed with --no-verify, or from
  # another checkout, does not.
  if [[ -f "$review_dir/$remote_sha.pass" ]] && git cat-file -e "${remote_sha}^{commit}" 2>/dev/null; then
    before=$(own_change "$remote_sha" || true)
    after=$(own_change "$local_sha" || true)
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

  echo "claude-review: reviewing $base..$local_sha ($local_ref -> $remote_ref) on claude_code:opus"
  review_file="$review_dir/$local_sha.md"
  diff_file="$review_dir/$local_sha.diff"
  {
    git log --format='commit %h%n%n%B' "$base..$local_sha"
    git diff "$base..$local_sha"
  } >"$diff_file"
  prompt_file="$review_dir/$local_sha.prompt"
  prompt="Review the change in $diff_file (its commit messages, then the diff of $base..$local_sha) in the repository \
at $repo_root; its working tree may differ, so trust the diff for what changed, and read every file by its path under \
$repo_root, since the run's own workspace is elsewhere. Follow $repo_root/REVIEW.md exactly: what failure \
looks like here, file-and-line evidence, the Important / Nit / Pre-existing tags, at most 3 nits, and a summary that \
starts with the finding count. Read the surrounding code before reporting a finding. Review only; change nothing. End \
your answer with one last line that is exactly \`VERDICT: BLOCK\` if there is at least one Important finding or you \
could not review the change, otherwise exactly \`VERDICT: PASS\`."
  # The chat the review is kept in belongs to the branch being pushed, not to the one checked out: `git push origin
  # other`, `git push --all` and the repair flow push a branch that is not HEAD.
  branch=${local_ref#refs/heads/}
  thread_file="$review_dir/thread-${branch//\//-}"
  # The change itself goes in the goal of a branch's first review while it fits the daemon's 50,000 characters, so that
  # the review starts from it rather than spending steps to read it; a larger one, and a later round's in the same
  # chat, are read from the file (the chat's earlier goals reach its judges as instructions: a diff in each would be
  # carried by every one of them).
  {
    printf '%s\n' "$prompt"
    if [[ ! -s "$thread_file" ]] && (($(wc -c <"$diff_file") <= 40000)); then
      printf '\nThe change (the same as %s):\n\n' "$diff_file"
      cat "$diff_file"
    fi
  } >"$prompt_file"

  # Dot's own model is named rather than left to the configured main model: the review is the one step that must not
  # drift. Research mode refuses every step that would change the repository or run a command's side effect, so the
  # review reads the code and cannot edit it; it may still read the web and keep a note. Its stdin is the goal's file
  # (item 1020): the hook's stdin is git's list of refs, and nothing may swallow the refs after this one. No goal mode:
  # a review's answer is its verdict, which a checker sending it back to work only makes longer.
  dotx=${DOTX:-$(command -v dotx || echo "$HOME/Desktop/Dot/.venv/bin/dotx")}
  review() {
    local args=(run --mode research --model "$1" --effort "$2" --no-goal-mode --review --no-input)
    if [[ -s "$thread_file" ]]; then
      args+=(--thread "$(cat "$thread_file")")
    fi
    "$dotx" "${args[@]}" - <"$prompt_file"
  }
  # The status of a run says whether it finished, not what it found: a BLOCK ends a run that exited cleanly, so the
  # verdict line decides. A review with no verdict (Claude refused the diff, as in item 1211, its plan was spent, or
  # the thread no longer exists) gets one more try on DeepSeek, the reviewer before item 1256, in a chat of its own:
  # the owner's choice of 2026-11-20.
  review claude_code:opus medium >"$review_file" 2>&1 || true
  if ! grep -qE '^VERDICT: (PASS|BLOCK)$' "$review_file"; then
    echo "claude-review: Claude gave no verdict; reviewing again on opencode_go:deepseek-v4.1-flash" >&2
    rm -f "$thread_file"
    review opencode_go:deepseek-v4.1-flash high >"$review_file" 2>&1 || true
  fi
  if thread=$(grep -oE 'thread thr_[a-z0-9_]+' "$review_file" | tail -n 1); then
    printf '%s\n' "${thread#thread }" >"$thread_file"
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
      echo "claude-review: the reviewer did not start or gave no VERDICT line; its last lines follow, and the whole" \
        "output is in $review_file" >&2
      tail -n 5 "$review_file" >&2 || true
      exit 1
      ;;
  esac
done
