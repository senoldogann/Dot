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
# read. A later push is reviewed from the newest commit of the branch that was reviewed here, one that passed or one
# whose review BLOCKed with findings (item 1954: a first push that BLOCKs never reaches the remote, so every round was
# read from where the branch leaves main, 29 times in one chat), and the reviewer is told to read only the files of that
# diff and, after a BLOCK, to check that its findings are fixed. After three earlier reviews of the branch that BLOCKed,
# the hook says so before the next one (REVIEW.md: reconsider the approach). A model that is not on the Claude plan is
# also told its budget (a number of calls and the time to write its verdict by), as DeepSeek stopped at the time limit
# in 21 of 61 reviews without a verdict.
#
# Before the review, two checks no model is asked for (item 2100), run from the scripts beside this hook (the checkout
# the hook is linked from, not the branch pushed, so that a branch cannot loosen what checks it) on that checkout's
# Python: every commit pushed carries the commit hook's stamp (scripts/hook_stamps.py: one made with the hooks off is
# refused), and a test the branch weakens is named, with why, in a `Test-change:` line of its commits
# (scripts/weakened_tests.py). The review is then given a section on the tests the change edits, which REVIEW.md's "Test
# changes" says how to answer.
set -euo pipefail

# The checkout this hook belongs to, through its link, and the Python and the scripts of its checks.
guard_root=$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/../.." && pwd)
guard_python="$guard_root/.venv/bin/python"
guards="$guard_root/scripts"

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

# Whether the review in file $1 BLOCKed with findings: its verdict is BLOCK and it counts at least one Important finding
# ("2 Important, 1 Nit"). A BLOCK that counts none (the review could not read the change, or ran out of its budget: the
# prompt asks for BLOCK then) read nothing for good, as a review with no verdict did.
blocked_with_findings() {
  [[ -f "$1" ]] && grep -q '^VERDICT: BLOCK$' "$1" && grep -qE '(^|[^0-9])[1-9][0-9]* Important' "$1"
}

# The newest commit of $1..$2, other than $2 itself, that was reviewed here: it passed, or its review BLOCKed with
# findings. Nothing is printed when there is none.
reviewed_since() {
  local commit
  for commit in $(git rev-list "$1..$2"); do
    [[ "$commit" == "$2" ]] && continue
    if [[ -f "$review_dir/$commit.pass" ]] || blocked_with_findings "$review_dir/$commit.md"; then
      echo "$commit"
      return 0
    fi
  done
}

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

  # The checks no model is asked for (item 2100): they read git alone and take a second.
  if [[ ! -x "$guard_python" ]]; then
    echo "claude-review: push stopped; $guard_python is missing: run \`uv sync\` in $guard_root" >&2
    exit 1
  fi
  status=0
  found=$("$guard_python" "$guards/hook_stamps.py" --base "$base" --head "$local_sha" 2>&1) || status=$?
  if ((status == 0)); then
    found=$("$guard_python" "$guards/weakened_tests.py" check --base origin/main --head "$local_sha" 2>&1) ||
      status=$?
  fi
  if ((status == 1)); then
    # What the checks found is written where a review's findings are, and named as a review's BLOCK names its file, so
    # that Dot's repair night hands it to the session to answer as it does a review (src/dotagent/repair/github.py,
    # push_branch). It carries no verdict: it is no review of the change, and no round of the three.
    checks_file="$review_dir/$local_sha.checks.md"
    printf '%s\n' "$found" >"$checks_file"
    printf 'claude-review: %s\n' "$found" >&2
    echo "claude-review: push stopped before the review; the review is in $checks_file. Fix what it found and push" \
      "again" >&2
    exit 1
  elif ((status != 0)); then
    echo "claude-review: push stopped; the checks before the review could not run (exit $status): $found" >&2
    exit 1
  fi

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

  # A change under src/dotagent can alter what the model is told (a tool's description, a prompt), and then the checked-in
  # prompt surface must be written again with it (item 1971). Its test runs on macOS only, so a pull request's Linux jobs
  # never see a stale one and main went red twice in a day: it is checked here, in seconds, before the model review. The
  # check reads the working tree, so it is made for the branch that is checked out. A push of the surface or the first
  # step's budget alone is checked too (item 1997).
  if [[ -f "$repo_root/scripts/prompt_surface.py" ]] && git diff --name-only "$base..$local_sha" |
    grep -qE '^(src/dotagent/|tests/fixtures/prompt_surface/|tests/smoke/test_context_budget\.py$)'; then
    if [[ "$(git rev-parse HEAD)" == "$local_sha" ]]; then
      # What is checked is the commit that is pushed: with uncommitted changes in what the render reads, a fixture written
      # and not committed would pass, and a source edit not committed would fail a commit that is fine.
      if [[ -n "$(git status --porcelain -- src/dotagent scripts/prompt_surface.py tests/fixtures/prompt_surface \
        tests/smoke/test_context_budget.py)" ]]; then
        echo "claude-review: push stopped; src/dotagent, scripts/prompt_surface.py, tests/fixtures/prompt_surface or" \
          "tests/smoke/test_context_budget.py has changes that are not committed, so the prompt surface and the first" \
          "step's budget cannot be checked for what is pushed: commit or stash them first" >&2
        exit 1
      fi
      status=0
      surface=$(uv run python scripts/prompt_surface.py --check 2>&1) || status=$?
      if ((status == 3)); then
        echo "claude-review: push stopped; the prompt surface is stale: $surface" >&2
        echo "claude-review: run \`uv run python scripts/prompt_surface.py\`, commit tests/fixtures/prompt_surface and push" \
          "again" >&2
        exit 1
      elif ((status != 0)); then
        echo "claude-review: push stopped; the prompt surface could not be checked (exit $status): $surface" >&2
        exit 1
      fi
      # The first step's budget (item 1997): its test is a macOS smoke test a pull request never runs, and main went red
      # on it four times in a day. The surface just checked is written from what the first step carries, so a change
      # to it (or to the budget) is what can move the numbers; the test is run then, in its own throwaway home.
      if git diff --name-only "$base..$local_sha" | grep -qE '^(tests/fixtures/prompt_surface/|tests/smoke/test_context_budget\.py$)'; then
        scratch=$(mktemp -d)
        trap 'rm -rf "$scratch"' EXIT
        status=0
        budget=$(DOT_HOME="$scratch/home" DOT_LOG_DIR="$scratch/logs" DOT_CHROME_DIR="$scratch/chrome" uv run pytest -q \
          -p no:warnings -p no:cacheprovider \
          "tests/smoke/test_context_budget.py::test_a_fresh_homes_first_step_stays_within_its_budget" 2>&1) || status=$?
        rm -rf "$scratch"
        trap - EXIT
        if ((status != 0)); then
          echo "claude-review: push stopped; the first step is over its budget, or its test failed (exit $status):" >&2
          grep -E '^E +(AssertionError|assert )' <<<"$budget" >&2 || tail -n 20 <<<"$budget" >&2
          echo "claude-review: take back what grew, or raise the budget in tests/smoke/test_context_budget.py with the" \
            "measured reason, and push again" >&2
          exit 1
        fi
      fi
    else
      echo "claude-review: $local_ref is not checked out, so its prompt surface is not checked here" >&2
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

  # Only what the branch changed since its newest reviewed commit is read (item 1954), unless main moved under the branch
  # since that commit (its change would then carry main's): then the change from where the branch leaves main.
  earlier=$(reviewed_since "$base" "$local_sha" || true)
  previous=""
  if [[ -n "$earlier" && "$(git merge-base origin/main "$earlier" 2>/dev/null || true)" == "$fork" ]]; then
    base=$earlier
    if blocked_with_findings "$review_dir/$earlier.md"; then
      previous="$review_dir/$earlier.md"
    fi
  fi

  review_file="$review_dir/$local_sha.md"
  diff_file="$review_dir/$local_sha.diff"
  changed_file="$review_dir/$local_sha.paths"
  git diff --name-only "$base..$local_sha" >"$changed_file"
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
could not review the change, otherwise exactly \`VERDICT: PASS\`. Read only the files the change touches (their paths \
are in $changed_file) and REVIEW.md: open another file only to settle a specific doubt about a changed line, say which \
and why, and do not survey the repository."
  if [[ -n "$previous" ]]; then
    prompt="$prompt This diff is only what the branch changed since an earlier review of it, which BLOCKed: that \
review is in $previous. Check that each Important finding in it is fixed by this diff or still stands, say which, \
then review this diff; do not repeat a finding that was fixed."
  fi
  # The chat the review is kept in belongs to the branch being pushed, not to the one checked out: `git push origin
  # other`, `git push --all` and the repair flow push a branch that is not HEAD.
  branch=${local_ref#refs/heads/}
  thread_file="$review_dir/thread-${branch//\//-}"
  # The tests the change edits, for the reviewer to answer for (REVIEW.md, "Test changes"; item 2100): in every
  # round's goal, the diff aside.
  status=0
  tests_brief=$("$guard_python" "$guards/weakened_tests.py" brief --base origin/main --since "$base" \
    --head "$local_sha" 2>&1) ||
    status=$?
  if ((status != 0)); then
    echo "claude-review: could not be reviewed: the tests the change edits could not be read: $tests_brief" >&2
    exit 1
  fi
  # The change itself goes in the goal of a branch's first review while it fits the daemon's 50,000 characters, so that
  # the review starts from it rather than spending steps to read it; a larger one, and a later round's in the same
  # chat, are read from the file (the chat's earlier goals reach its judges as instructions: a diff in each would be
  # carried by every one of them).
  {
    printf '%s\n' "$prompt"
    if [[ -n "$tests_brief" ]]; then
      printf '\n%s\n' "$tests_brief"
    fi
    if [[ ! -s "$thread_file" ]] && (($(wc -c <"$diff_file") <= 40000)); then
      printf '\nThe change (the same as %s):\n\n' "$diff_file"
      cat "$diff_file"
    fi
  } >"$prompt_file"

  # Which models review this change, in order, and for how long in all: `[code_review]` of Dot's config, as `dotx review
  # plan` reads it for a change of this many changed lines and these paths. A plan that cannot be read stops the push.
  dotx=${DOTX:-$(command -v dotx || echo "$HOME/Desktop/Dot/.venv/bin/dotx")}
  lines=$(git diff --numstat "$base..$local_sha" | awk '$1 != "-" { n += $1 + $2 } END { print n + 0 }')
  if ! plan=$("$dotx" review plan --lines "$lines" --changed "$changed_file" --guide "$repo_root/REVIEW.md" 2>&1); then
    echo "claude-review: could not be reviewed: \`dotx review plan\` failed: $plan" >&2
    echo "claude-review: push again once it answers, or push with --no-verify" >&2
    exit 1
  fi
  limit=$(awk '$1 == "limit" { print $2 }' <<<"$plan")
  size=$(awk '$1 == "size" { print $2 }' <<<"$plan")
  route=$(awk '$1 == "route" { print $2 }' <<<"$plan")
  echo "claude-review: reviewing $base..$local_sha ($local_ref -> $remote_ref), $lines changed lines ($size, route" \
    "${route:-default})"

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
    # A model that is not on the Claude plan reads the whole repository at about ten seconds a step unless it is told its
    # budget: a number of calls and the time by which its verdict is written (item 1954).
    local goal_file="$review_dir/$local_sha.goal"
    {
      cat "$prompt_file"
      if [[ "$1" != claude_code:* ]]; then
        printf '\nYour budget: at most 40 tool calls and %d minutes in all. Read the change file first, then only the ' \
          "$(($3 * 8 / 10 / 60))"
        printf 'files it names. When about three quarters of the calls or the time are used, stop reading and write your '
        printf 'findings and the VERDICT line from what you have: a review that ends without the VERDICT line is lost.\n'
      fi
    } >"$goal_file"
    "$dotx" "${args[@]}" - <"$goal_file" >"$review_file" 2>&1 &
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
