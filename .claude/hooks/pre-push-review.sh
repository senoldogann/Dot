#!/usr/bin/env bash
# Git pre-push hook: Dot reviews the commits about to be pushed, following REVIEW.md, in research mode (read-only). Its
# chat is kept under Dot's Code reviews (`--review`), apart from the owner's own chats. An Important finding stops the
# push.
#
# The models are the owner's to choose (item 1663): `[code_review]` of Dot's config.toml, which Settings › Models ›
# Code review changes, and which the hook reads through `dotx review plan` (it parses no TOML). By default the review
# runs on the owner's Claude plan (`claude_code:opus`, effort medium: the owner's choice of 2026-11-20, after reviews on
# DeepSeek took 6 to 27 minutes and 3 to 14 million tokens), and a review with no verdict (Claude refused the diff, as in
# item 1211, or its plan was spent) is done again on `opencode_go:deepseek-v4.1-flash` at effort high, in a chat of its
# own. A change of at most `small_max_lines` changed lines that touches no path REVIEW.md lists under "Always needs a
# human" goes to `small_model` first, when one is chosen. When no model of the list gives a verdict, or the review runs
# past `max_minutes` (15 by default; the run is stopped then), the push stops with "could not be reviewed" and why:
# never a silent pass.
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

  # Which models review this change, in order, and for how long in all: `[code_review]` of Dot's config, as `dotx review
  # plan` reads it for a change of this many changed lines and these paths. A plan that cannot be read stops the push.
  dotx=${DOTX:-$(command -v dotx || echo "$HOME/Desktop/Dot/.venv/bin/dotx")}
  changed_file="$review_dir/$local_sha.paths"
  git diff --name-only "$base..$local_sha" >"$changed_file"
  lines=$(git diff --numstat "$base..$local_sha" | awk '$1 != "-" { n += $1 + $2 } END { print n + 0 }')
  if ! plan=$("$dotx" review plan --lines "$lines" --changed "$changed_file" --guide "$repo_root/REVIEW.md" 2>&1); then
    echo "claude-review: could not be reviewed: \`dotx review plan\` failed: $plan" >&2
    echo "claude-review: push again once it answers, or push with --no-verify" >&2
    exit 1
  fi
  limit=$(awk '$1 == "limit" { print $2 }' <<<"$plan")
  size=$(awk '$1 == "size" { print $2 }' <<<"$plan")
  echo "claude-review: reviewing $base..$local_sha ($local_ref -> $remote_ref), $lines changed lines ($size)"

  # One try: Dot's run on model $1 at effort $2, research mode, no goal mode (a review's answer is its verdict, which a
  # checker sending it back to work only makes longer), its stdin the goal's file (item 1020: the hook's stdin is git's
  # list of refs, and nothing may swallow the refs after this one). Research mode refuses every step that would change
  # the repository or run a command's side effect, so the review reads the code and cannot edit it. After $3 seconds
  # it is sent SIGTERM, on which `dotx run` asks Dot to stop the run, and SIGKILL 30 seconds later; $timeout_file says
  # that the time ran out.
  timeout_file="$review_dir/$local_sha.timeout"
  review() {
    local args=(run --mode research --model "$1" --effort "$2" --no-goal-mode --review --no-input)
    if [[ -s "$thread_file" ]]; then
      args+=(--thread "$(cat "$thread_file")")
    fi
    "$dotx" "${args[@]}" - <"$prompt_file" >"$review_file" 2>&1 &
    local pid=$!
    (
      sleep "$3"
      touch "$timeout_file"
      kill -TERM "$pid" 2>/dev/null || exit 0
      sleep 30
      kill -KILL "$pid" 2>/dev/null || true
    ) </dev/null >/dev/null 2>&1 &
    local watcher=$!
    wait "$pid" || true
    pkill -TERM -P "$watcher" 2>/dev/null || true
    kill -TERM "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
  }

  # The status of a run says whether it finished, not what it found: a BLOCK ends a run that exited cleanly, so the
  # verdict line decides. A try with no verdict hands the change to the next model of the plan, in a chat of its own.
  rm -f "$timeout_file" "$review_dir/$local_sha.silent"
  started=$(date +%s)
  silent=""
  while read -r kind model effort; do
    [[ "$kind" == "try" ]] || continue
    left=$((limit - ($(date +%s) - started)))
    if ((left <= 0)); then
      touch "$timeout_file"
      break
    fi
    echo "claude-review: on $model, effort $effort"
    review "$model" "$effort" "$left"
    if [[ -e "$timeout_file" ]] || grep -qE '^VERDICT: (PASS|BLOCK)$' "$review_file"; then
      break
    fi
    echo "claude-review: $model gave no verdict; its last lines:" >&2
    tail -n 3 "$review_file" >&2 || true
    cat "$review_file" >>"$review_dir/$local_sha.silent"
    silent="$silent${silent:+, }$model"
    rm -f "$thread_file"
  done <<<"$plan"
  # A verdict written as the time ran out still counts: it is the review's.
  if [[ -e "$timeout_file" ]] && ! grep -qE '^VERDICT: (PASS|BLOCK)$' "$review_file" 2>/dev/null; then
    echo "claude-review: could not be reviewed: the review ran past $((limit / 60)) minutes ([code_review] max_minutes)" \
      "and was stopped; what it wrote is in $review_file. Push again, or push with --no-verify" >&2
    exit 1
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
      echo "claude-review: could not be reviewed: no model gave a verdict ($silent); what each wrote is in" \
        "$review_dir/$local_sha.silent. Push again, or push with --no-verify" >&2
      exit 1
      ;;
  esac
done
