# Review guide

Read by `/code-review` and Code Review for Claude Code. Dot runs with the owner's rights on their Mac: a change is judged
by what it lets a run do as much as by how it reads.

## What failure looks like here

- A run does something the policy should have stopped: it pays, enters a credential, posts, sends a message, changes a
  system setting or escapes its sandbox without the approval its mode requires.
- A secret reaches a model, the logs, the events, the API or the web UI unmasked.
- The daemon's API answers a request it should refuse: a missing or wrong token, a request from somewhere it shouldn't
  come from.
- A run says "done" when the workspace shows it isn't, or an action is not recorded and can't be undone.
- A page under `docs/` says Dot does something it no longer does.

## Always flag (Important)

- Behavior that contradicts the PR's stated intent; a bug fix that patches a symptom instead of the root cause
- An error swallowed, caught by a catch-all, or hidden behind a fallback
- Data from outside (a file, a service's answer, a web page, a tool's output) used before it is validated into a model
- A test that calls a model or reaches outside this Mac without a `live`, `internet` or `hosted` mark, or that touches
  the real `DOT_HOME`
- A change in what Dot does without the matching change under `docs/`
- A change that makes a line in `CLAUDE.md`, `CONTRIBUTING.md` or `docs/` stale

## Checks made from reviews

A finding that comes back gets a check, not another comment: a test or a gate step that fails the next time. Put the
check in the same pull request as the finding, and name it here. `tests/unit/test_review_rules.py` fails if a line
here points at a test that no longer exists.

- `tests/smoke/test_secret_fields.py::test_ask_mode_types_a_one_time_code_the_user_gave` — a one-time code the user
  gave is typed and the form submits, while a code found on the page is refused (`test_ask_mode_refuses_to_type_a_one_time_code`).
- `tests/unit/test_ask_user_notify.py::test_a_left_question_notifies_the_user` — a question left for the user sends a
  notification.
- `tests/unit/test_limits.py::test_a_run_has_a_default_ceiling_and_zero_turns_it_off` — a run has a default
  120-minute, 10,000,000-token ceiling, and 0 turns a bound off.
- `tests/smoke/test_chat_permits.py::test_a_permit_never_answers_money_nor_a_step_of_dots_own_initiative` — a
  "don't ask again in this chat" never answers money nor a step of Dot's initiative, and answers one of a run that read
  outside content, as the owner chose (item 866).

## Evidence and format

- Cite file and line for every finding; read the code instead of guessing from names
- Tag each finding **Important** (introduced by this change, must fix), **Nit** (optional) or **Pre-existing** (was
  already there; don't block on it)
- At most 3 nits; skip what `ruff format`, `ruff check`, `mypy`, `cargo fmt` and `clippy` already enforce
- When a bug has siblings, name the bug class and list every instance
- Start the summary with the finding count, e.g. "2 Important, 1 Nit"; with nothing to fix, say "No issues found"
- A pull request that carries several items (a commit `Item N: ...` each): name the item of every finding, and check
  each item's entry in `roadmap/` (and `changes/`, when what Dot does changes) against its own commit

## Re-reviews

- After the first full pass, review only the new diff
- Don't repeat findings that were fixed or answered with a reason
- After 3 rounds, stop and ask the author to reconsider the approach (the hook counts the rounds and says so; a push
  that only brings main in is not a round)

## Security review brief

A change under "Always needs a human" is also read by an independent agent that knows only the branch. Start it at the
first push, beside the review and the gate, and take its findings in the same push as the review's. The brief: the
worktree it may read; what it must not do (edit, commit, push, write outside a `mktemp -d` folder, feed a shell a
destructive line: probes go in files); the change in a paragraph; and what a finding is, an input that gets through with
the verdict it gets and its twin's, while a limit the docs already state is not one. It reports findings or says plainly
that it found none, and what it tried.

## Skip

- `uv.lock`, `desktop/src-tauri/Cargo.lock`, `desktop/package-lock.json`
- `docs/images/`, `desktop/icons/`, `desktop/src-tauri/icons/`
- `ROADMAP.md`, `roadmap/`, `CHANGELOG.md`, `changes/` (check only that the entry matches the change)

## Always needs a human

- `src/dotagent/policy/` (the policy, the shell guard, the sandbox, pre-approved spending)
- `src/dotagent/agent/approvals.py`, `src/dotagent/agent/review.py`
- `src/dotagent/secrets.py`, `src/dotagent/server/guard.py`, `src/dotagent/server/pairing.py`,
  `src/dotagent/server/routes_keys.py`
- `src/dotagent/undo/`
- `desktop/src-tauri/capabilities/`, `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/build.rs` (the commands the app manifest names, each behind a permission of its own)
- `desktop/src-tauri/src/installer.rs` (what the installer runs and with what environment, never over another Dot)
- `desktop/tools.lock` (the programs the app carries, pinned by hash)
- `desktop/fetch-tools.sh` (how those programs are fetched, checked and signed)
- `desktop/release.sh` (what a release builds, signs and publishes, and in what order)
- `.github/workflows/`, `pyproject.toml` dependencies
