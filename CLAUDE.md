# Dot

Dot is an always-on autonomous agent for macOS: a Python 3.13 package (`src/dotagent`, the `dotx` command) whose daemon
serves a REST API and a web UI (plain JavaScript modules and CSS under `src/dotagent/server/static/`, no build step), and
a Tauri desktop shell in Rust (`desktop/`). It runs with the owner's rights on their Mac, so a change is judged by what
it lets a run do as much as by how it reads.

## Goals and non-goals

- Goal: work on a goal until it is done, acting without asking, but stop for what it must not decide alone (money,
  credentials, public posts, messages sent for the owner, privilege, system settings) — in every mode but `yolo`, which
  asks nothing at all (item 443) — record everything it does, and stop at once when told to.
  [Safety](docs/safety.md) is the contract.
- Non-goal: any platform but macOS (it depends on PyObjC and drives macOS's own applications).

## Checks

Point `DOT_HOME`, `DOT_LOG_DIR` and `DOT_CHROME_DIR` at throwaway folders first, so no test touches the owner's own Dot.
While working, run the tests of what changed (`uv run pytest tests/unit/test_x.py tests/smoke/test_y.py -q`), not the
whole gate. Run the whole gate once, when the change is done; `.github/workflows/gate.yml` runs it again on the pull
request. `-n auto` spreads the tests over the Mac's cores.

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest tests/unit -q -n auto
uv run dotx eval check
uv run pytest tests/smoke -q -n auto -m "not live and not browser and not internet and not desktop and not hosted"
npm run check
```

- A change to a tool, the policy or the loop: `dotx eval check` must pass.
- A change the browser tests cover: add them with `-m "not live and not internet and not hosted and not desktop"`.
- `desktop/src-tauri`: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`.
- A web UI change: `npm run check` (Biome, `tsc` over the JSDoc, `node --test tests/js`; `npm ci` once, Node 22 or later,
  a development requirement only), and click through the affected screen in a browser, on a phone-sized window too.

[CONTRIBUTING.md](CONTRIBUTING.md) has the conventions for code, tests and pull requests, and
[docs/development.md](docs/development.md) what each test marker needs and how the agent is measured.

## Conventions not to break

- A test calls no model and reaches nothing outside this Mac, except those marked `live`, `internet` and `hosted`.
- A change in what Dot does updates the page under `docs/` that describes it, in the same pull request.
- `PLAN.md`, `reports/` and `config.toml` are the owner's local files and are never committed.

## Review

[REVIEW.md](REVIEW.md) says what the code review looks for here. `.claude/hooks/pre-push-review.sh` runs that review
before every push, on the `claude` command's subscription; its header says how to install and skip it. That hook is the
one review a change gets: `/code-review` or a review subagent is not run on top of it. A finding is fixed or answered
and pushed again, and the hook reads only the new commits. `/security-review` runs only when a change touches a path
REVIEW.md lists under "Always needs a human".

## Keeping a change short (item 540)

A change that takes days is many pushes, each waiting on the review and the gate. What cuts them, no check cut:

- **One push per round.** Fix every finding and nit of a review together and push once; never push a nit alone. Run
  what changed first (`ruff`, `mypy`, the tests of its files) so that the gate finds nothing.
- **The approach before the code, for the policy, the shell guard and the sandbox.** Write in `roadmap/<item>.md` what
  must hold and what only asks. What must hold goes in the kernel (the sandbox profile, `[policy] sealed_paths`); the
  guard asks by the names it can read, and the docs say what it does not read. Do not chase spellings: each one closes
  one and opens the next (items 531 and 534 took eleven rounds each). The hook says when a branch has had three
  BLOCKs: stop, take the structural option or split the change.
- **The independent `/security-review` starts with the first push** of a change under "Always needs a human" (the
  brief is in REVIEW.md), beside the review and the gate rather than after them.
- **A pull request carries a batch (item 541).** What a session builds goes in one branch and one pull request, so that
  the review, the gate and the merge run once: several roadmap items, each its own commit `Item N: ...` with its own
  `roadmap/<n>.md` (and `changes/<n>.md` when what Dot does changes), the numbers taken together (`scripts/next_item.py
  3`). Merge a batch with `gh pr merge --rebase`, so that each item stays one commit that can be reverted alone (a pull
  request of one item may be squashed). Order the commits by item and say in the body which is which. Keep a part out of
  the batch when it needs the owner's look (a change under "Always needs a human" waiting on them must not hold the
  rest) or is a risk that should be reverted alone.
- **The item numbers** come from `uv run python scripts/next_item.py [count]` right before the first push (main, open
  pull requests and pushed branches all hold numbers), and the pull request is made from the entries (`gh pr create
  --title "$(uv run python scripts/pr_body.py N M --title)" --body "$(uv run python scripts/pr_body.py N M)"`).
- **Merging main** into a branch whose own change is unchanged costs no review (the hook sees it); do it when the
  branch is ready, never while a test run of the same worktree is going.
- **A failure that is not the change's** (a known flaky test, `main` red): rerun the job (`gh run rerun <id> --failed`)
  and say so in the pull request; fix `main` in the pull request that is blocked by it only when it is one line.
- **Browser tests**: the files of the screens touched (and `test_phone_*` when the layout changed); the whole suite
  runs at night.

## How much process a change needs

- Small: prose only (`docs/`, `ROADMAP.md`, `roadmap/`, `CHANGELOG.md`, `changes/`, comments), or under about 50 changed
  lines outside the
  "Always needs a human" paths. No `PLAN.md`, no review but the pre-push hook, the checks of what changed; push it.
- A roadmap or changelog entry goes in the pull request of the change it records, not in a pull request of its own,
  in files of the change's own: `roadmap/<item>.md` and `changes/<item>.md` (their READMEs say how), not in ROADMAP.md
  or CHANGELOG.md, which changes made side by side would all write at the same lines.
- Everything else: done criteria (in `PLAN.md` when it takes several steps), the tests of what changed while working,
  the whole gate once at the end, the pre-push review, the pull request.
