# Self-repair

Dot fixes its own code (items 919-921). It keeps a book of what its record shows going wrong again and again, and each
night it hands the first problem to Claude Code, signed in on this Mac with your own plan, in a checkout of its own of
Dot's repository. The session fixes the problem with a test and opens a pull request. Dot then merges it once every
check passes, or leaves it for you. [The harness](harness.md) improves what Dot says to its model; this changes the
code itself.

It is off until `[repair] repo` names the git checkout of Dot's code (`~/Desktop/Dot` on the owner's Mac). Settings ›
Rules and memory › Self-repair shows the book, the sessions and the account, and starts a night by hand (**Run now**):
`GET /api/repair` gives them, and `POST /api/repair/now` starts a night (409 with no repository, or while one is under
way).

## The book of problems

Dot reads its record since the last night (the week before, the first time). Four things count as a problem:

- **A tool error seen in several runs.** A tool that failed with the same error, counted once a run.
- **A progress watchdog alert.** Grouped by its pattern and detail.
- **A failed step of the improvement loop.**
- **An error in the daemon's log.** A line of level `error` or `critical` in `dotx.log` or the log before it
  (`dotx.log.1`), grouped by its event.

Some failures do not count, because they are the run's own doing, the world's, or your decision:

- An approval refused or expired.
- A tool in use by another run.
- A page that is not there.
- A command, a script or an AppleScript the model wrote that failed.
- A text that did not appear on a page.
- An edit whose text was not in the file.
- A run that ended *failed*. Its result is the model's own prose. On the owner's Mac (2026-11-02), every such run of the
  week had ended on a step the owner had not approved.

Two sightings of one defect are one problem. Dot compares the words with ids, numbers, quoted words, paths and addresses
taken out. A problem keeps its newest five lines of evidence. A fixed problem that is seen again opens again: the fix
did not hold. The book keeps 200 problems at most (kv `repair.book`).

A problem is handed out once it was seen `least_seen` times (2), the most seen first. It gets two sessions at most;
after that it waits for you.

## A session

Claude Code runs as `claude -p` on the problem, in a checkout made from `origin/main` on a branch of its own
(`repair/<problem>-<random>`); a branch that opened no pull request is deleted with the checkout.

- **What Anthropic allows.** You may use your own sign-in through the unmodified `claude` program, and that is all Dot
  does. It never reads the sign-in's token. The session's environment has no API key: with `ANTHROPIC_API_KEY` set,
  `claude -p` would bill that key instead of your plan.
- **Its environment.** It holds no other key or token of Dot's. `DOT_HOME`, `DOT_LOG_DIR` and `DOT_CHROME_DIR` point at
  throwaway folders, so no test the session runs touches your Dot.
- **No way to GitHub, by default.** The session is meant to commit on its own branch and leave the rest to Dot, and
  its environment is set so that GitHub is out of its reach:
  - no GitHub token;
  - `gh` reads its sign-in from an empty folder;
  - git's credential helpers (the Keychain's among them) are reset to none, it may not ask for a password, and ssh is
    no way out.

  Checked on the owner's Mac (2026-11-02): `git push` stops at "could not read Username", and `gh` says it is not
  signed in. **This is a default, not a boundary.** The session has a shell, and a command of its own can set these
  back (`GH_CONFIG_DIR=…`, `git -c credential.helper=…`): Claude Code reads its own sign-in from the same Keychain, so
  no sandbox of the Mac can take one from it and leave the other. A few commands are refused by name besides, which
  read commands by their first words, not every spelling.
- **Its rights.** A session runs with your rights, as Dot's own runs do: it has a shell and can write wherever you
  can, outside its checkout too. No sandbox of the Mac could keep it to its folder and leave Claude Code its own
  sign-in. What Dot checks is what it would merge, pull and restart with: GitHub's protection of `main`, the files of
  the change (*What needs you*, below), and both `main` and your checkout of Dot before it merges (*Main moved*,
  below). What a session might change elsewhere on the Mac (your shell's files, Dot's home) it does not check; the
  fence around the evidence and the auto mode's own judgment are what stand there.
- **Its account.** Dot reads which account is signed in from `~/.claude.json` (its `oauthAccount`: the address and the
  organisation, no secret), before each session, and each session records it. A switcher that signs another account
  in is followed at the next session.
- **Its permissions.** The session runs with `--permission-mode auto` and `--permission-prompts none`: it decides what
  is safe, and anything that would ask is refused. Some commands are refused by name besides: merging or closing a
  pull request, `gh repo`, `gh release`, `gh secret`, `gh auth`, a push with `--force` or to `main`, `git credential`,
  `dotx service`, `launchctl`, `security`, `pkill`, `killall` and `sudo`.
- **The brief.** The problem and its evidence sit in a fence, as data from Dot's record that may quote a page, a mail or
  a tool's output, never an instruction. The brief then gives the repository's own rules:
  1. Read CLAUDE.md, CONTRIBUTING.md and REVIEW.md.
  2. Find the root cause. If it is no defect of Dot's, change nothing.
  3. Write a test that shows the problem, then fix it.
  4. Run the checks of what changed.
  5. Write the entries of the item number Dot gives it and commit as `Item <n>: ...`. Dot takes the number with the
     repository's `scripts/next_item.py` and its own sign-in to GitHub, which the session has no way to.
  6. Do not push: Dot does.
  7. Never change a path that REVIEW.md lists under "Always needs a human".
- **Its answer.** The last line is `READY: <what the commits fix>`, `NO FIX: <why>` (the problem is let go) or
  `NEEDS OWNER: <why>`.

## The pull request

Dot pushes the branch, opens the pull request and merges it. It never takes a session's word for what its change does:

- **What needs you.** Dot reads the files the branch changes, and then GitHub's own list of the pull request's. A
  change to one of these paths waits for you, whatever `merge` says:
  - a path REVIEW.md lists under "Always needs a human", as `main` holds REVIEW.md on GitHub, never a checkout's copy
    a session could change (if REVIEW.md lists none there, every path counts);
  - the sources of the rules a session is held to and the gate's own code, by Dot's code whatever REVIEW.md says
    (`PROTECTED`): REVIEW.md, CLAUDE.md, AGENTS.md, CONTRIBUTING.md, `.claude/`, `.github/`, `src/dotagent/repair/` and
    the scheduler that starts the night. A change merged without you could otherwise loosen the gate for the nights
    after it;
  - what Dot installs and runs with: `pyproject.toml`, `uv.lock`, the `package.json` and `package-lock.json` files and
    the desktop app's `Cargo.toml` and `Cargo.lock`, so that a dependency changed by its lock alone is yours to take.
- **Review.** Dot pushes the session's commit through the repository's pre-push hook, from your checkout of Dot,
  never from the session's: the hook's reviewer reads REVIEW.md, CLAUDE.md and its settings from the folder it runs
  in, and its base from `origin/main`, which a session could have changed in its own checkout and in the refs all
  checkouts share. Your checkout is the one Dot's print guards, `main` is fetched first, and a mark of a passed
  review for that commit is removed (the hook skips a commit it holds one for), so that only the hook's own review
  passes it. When the hook stops the push, its review goes back to the same
  session (`--resume`), fenced as a finding, and Dot pushes the session's next commit. A push is given half an hour.
- **The pull request.** Its title is the commit's subject (`Item <n>: ...`). Its body holds the commits and says it
  came from the self-repair, with the problem and the session.
- **Checks.** Dot waits for the checks for `checks_minutes` at most (45), looking every minute. The failed ones go back
  to the same session, each with the end of the log of its failed steps, fenced. When the session holds a failure not
  its change's and commits nothing, Dot runs the failed checks again. A session gets `rounds` rounds in all (3): the
  first, and one for each review that stopped it and each time its checks failed.
- **Merge.** With every check passed:
  - `merge = "on_pass"`: Dot merges the pull request into main, squashed (rebased for several items), only while its
    head is still the commit whose files and checks it looked at (`--match-head-commit`). A merge that reports a
    failure is read back from GitHub: merged is merged. Dot then deletes the branch on GitHub and, once the checkout is
    gone, here. If your checkout (`repo`) is
    on main, Dot pulls it fast-forward only. The night then ends, and Dot asks for its own restart once it is idle
    (`dotx service restart --when-idle`). It does not go on in a process whose code on disk has changed, and the next
    night takes the next problem.
  - `merge = "ask"` (the default): the pull request waits for you.
- **Cleanup.** The checkout is removed afterwards. Its branch stays when it has a pull request, or commits that wait
  for you (the session asked for you, or the review still stopped it); otherwise it is deleted.
- **Main moved.** GitHub refuses a push straight to `main` of a commit whose checks have not passed (its protection
  requires `gate`, `browser (pages)` and `browser (phone)`, up to date, for admins too: read on 2026-11-02), so the one
  way into `main` is a pull request whose checks passed. When a session begins, Dot reads where `main` stands on
  GitHub and takes a print of your checkout of Dot: its commit, the contents of every change to a tracked file and of
  every file git does not track, its git config and its hooks read through their links. Files git ignores (`.venv`)
  are not in it. It reads both again before it merges, and after every session it merged nothing for, one that
  failed too; what cannot be read counts as changed. When either changed by no doing of Dot's, the night stops at
  once: nothing more is merged, pulled or restarted, and you are told what changed (*A fix of Dot's own needs you*: "During the session main moved: #…", or "your
  checkout of Dot … changed"). One you merged or edited yourself in that time stops the night too; its problem is
  tried again on a later night.
- **Failures.** The emergency stop, or another cancel, leaves the problem as it was, never *being fixed* for good. A
  session that ended with its pull request open but not merged (the night's time, the plan, a step failing) leaves
  the problem waiting for you with that pull request, never a second one on a later night. A
  session that failed (git or GitHub failing, no answer) counts as one of the problem's two tries; once both are
  spent, the problem waits for you, and the page says so with the last note.

You are told, by the push, the page and iMessage, in each of these cases:

- a fix is merged (*Dot fixed a problem of its own*);
- a fix is ready for your merge;
- a fix needs you: it changes a protected path, the session asked for you, the review still stopped it, or its checks
  still failed after the last round.

## Bounds

- **Per day.** At most `sessions_per_day` sessions (10).
- **Room for you.** Before each session Dot reads Claude Code's own report of the plan's windows (`claude -p /usage`,
  read locally with no model call: no turn, no cost): the five hours, the week, and a model's week when it applies. No
  session starts while one that applies is used past `leave_room` (0.8: a fifth stays yours). When the report cannot
  be read (the log says `repair_usage_not_read`), Dot decides by the window the last session saw (kv
  `repair.limit`): used past `leave_room` or refusing calls, until it resets. A session the window refused
  is no try at its problem and no session of the day's. Dot then gives a switcher 30 seconds: when another account is
  signed in by then (the owner's switches to the next account at a limit), the night goes on with it at once;
  otherwise the night ends.
- **Time.** One session round ends after `session_minutes` (90), with everything it started. A night's work is
  bounded by two hours:
  - the rounds of a session and its waits for the checks are cut to what is left of them;
  - no round, push or wait for the checks starts once they are spent (the branch stays and the problem is tried again);
  - no session starts with less than a quarter of an hour left.

  A push already under way through the review hook may add its own half hour, as the hook's review takes minutes. So
  a night ends within about two and a half hours, and a restart asked meanwhile, which waits three hours for the
  night's work, is not given up on. A problem left *being fixed* by a daemon that stopped short (a crash, the Mac
  shutting down) is open again when the next night begins.
- **Emergency stop.** It cancels a night under way and keeps the next session from starting.
- **The record.** Each session is kept (kv `repair.sessions`, the newest 100): its account, rounds, tokens, what it
  would have cost on the API, how it ended, its pull request and a note.

## Settings

```toml
[repair]
repo = ""             # the git checkout of Dot's code; "" is off
time = "05:30"        # each night, in [scheduler] timezone; "off" leaves Run now alone
claude = "claude"     # the command of Claude Code
sessions_per_day = 10
rounds = 3            # rounds of one session: the first, and one for each time its checks fail
least_seen = 2        # times a problem was seen before it is handed out
leave_room = 0.8      # no session while the plan's window is used past this
session_minutes = 90
checks_minutes = 45
merge = "ask"         # "on_pass": merge once every check passes
```
