# The freedom contract

The owner's word of 2026-11-24: improve Dot, but never break its freedom, **yolo mode and free mode**, and make computer
use, browser use and the other autonomous work better and faster. This page is that promise in one place: what the two
modes guarantee, the exact exceptions and the code that makes each, what they record, who may change them and how a
change that breaks them is caught. [Autonomy](autonomy.md) and [Safety](safety.md) describe the modes; this page is the
contract, and `tests/unit/test_freedom_contract.py` is what makes it executable.

## The guarantee

**A run in yolo, and every run while free mode is on, asks the owner nothing, and every tool action runs unasked.**

- **Yolo** (`approvals = "yolo"` of the run; the chat's pill "Pay", item 443): the gate waives every question and
  every hand-off, each with the reason it would have asked for: the question that outside content raises, the one about
  Dot's own files (`_OWN_FILES_REASON`), money and credentials, a login filled from a vault (`_VAULT_REASON`), a message
  or a post in the owner's name, a release, a pre-approved spend, the preset's and the owner's `ask` rules', and the
  refusal of a read of their secrets or of data sent out; all but the payment of the table below. The step runs and its
  record says what it would have asked.
- **Free mode** (`[policy] free = true`, Settings › Rules › How Dot asks, `PUT /api/policy/free`, item 922): the same
  for every run of every chat, whatever its own approval mode, from its next step on (a run reads the switch for its own questions when it starts), and the payment, the message or
  mail sent for the owner, the public post and the release too, and the payment (the gate's `held` is empty while it is
  on).

**Freedom costs no speed.** The gate of such a run is one policy decision and one record; it awaits no answer and no kept
step (the pace the owner set for a site, which spaces the public actions of the browser, is the one wait left, and is
theirs), and a call's `tool_result` event keeps `waited_s`, the time it spent at the gate. A change that puts a wait in
that path breaks the contract as an ask does. Outside the gate, a desktop step that would take the Mac's own pointer or
keyboard while the owner is at the Mac waits for them to step away (item 1890, the owner's own rule of 2026-11-25,
`[desktop] input_only_when_away`): it asks nothing, in every mode, and is in
[Browser and computer use](browser-and-computer-use.md). The background driver of `[desktop] background_driver` (item
2007) changes how such a step is sent, not what the gate answers about it (`test_freedom_contract.py` classifies
every desktop row alike with it on).

## The exceptions, with the code that makes each

What still waits for the owner's yes. Nothing else does.

| Where | What asks | The code |
| --- | --- | --- |
| Yolo, a chat the owner opened (item 860) or one Dot opened on its own initiative (item 2069) | a payment, handed to them by the money stop (one within the caps they pre-approved for its site is their question instead), never answered by a model, a schedule's run in their chat included | `payment_guard`, `PAYMENT_REASON` and `_PAYMENT_FLAGS` (`financial`) in `policy/engine.py`; `held` in `RunGate._decide_spent` |

That is yolo's one ask, the owner's word of 2026-11-28 (item 2069): "Yolo modda hiçbir şey sormasın, sadece ödeme
işlerinde sorsun". Before it, item 860 asked a message or mail sent for the owner and a public post in their chats too,
and had the review model judge such a step of a schedule's run there; item 850 asked a message, a post and a release
in a chat Dot opened on its own initiative, in yolo as in every mode. In yolo all of these go now, unasked and recorded,
**in a chat Dot opened on its own initiative too**: a run of Dot's own initiative in yolo (`[initiative] approvals`,
yolo by default) sends a mail, posts and puts a change live without the owner's yes, and only its payments wait for
them. A run nobody watches (a chat a schedule or a watcher opened) asked nothing in yolo and still asks nothing, a
payment neither.

Outside yolo and free mode, a chat Dot opened on its own initiative still asks before a message, a post or a release, in
`ask`, `auto` and `supervised` (item 850: `initiative_guard`, `_INITIATIVE_REASON` and `_INITIATIVE_FLAGS` in
`policy/engine.py`); that is no part of this contract, whose modes are yolo and free.

Free mode keeps not even the payment. What it keeps is below, as known asks: they are not decisions of the gate.

## What still refuses

A refusal is not a question, and it stands in yolo and in free mode. This is today's whole list; a change that makes a yolo
or free run deny something it allowed is a regression too.

- A `research` run changes nothing but its notes and memory (`mode_gate`).
- The pace of a site holds for the public and social actions of the browser, in every mode (`[pace]`, `RunGate._keep_pace`).
- A run that read outside content has the review model check the steps an instruction planted there is after, and its
  refusal stands (`[policy] yolo_taint_check`, on by default, item 525; `false` turns it off). Nobody is asked.
- A step of a yolo run that read outside content whose words would send a secret Dot holds out of the Mac is refused;
  free mode records it instead (item 1097).
- The desktop tools stay away from password managers and Keychain Access in every mode.
- **A run nobody started keeps the sandbox in yolo and free mode** (item 2016, the owner's word of 2026-11-27,
  `[shell] sandbox_unattended`, on by default): the processes of a run of a schedule, a watcher, a hook, Dot's own
  initiative and what goes on from them (`SwitchedSandbox.for_run`, `policy/sandbox.py`; which run it is,
  `unattended_origin` in `agent/manager.py`) can neither read nor change Dot's own files and `[policy] sealed_paths`,
  nor what Dot loads and runs, nor hand work to launchd, Shortcuts or Launch Services, nor reach the clipboard
  ([Safety](safety.md)). macOS refuses such a step (`Operation not permitted`); the gate asks nothing new, waits for
  nothing and decides every call as before, and the rest of the task's reach (the network, the owner's files, their
  programs) is unchanged. Dot's own tools, which act in the daemon's process, keep the same seal for such a run (item
  2026, `RunGate._keep_seal`, `sealed_step` in `policy/sandbox.py`): a step of `write_file`, `edit_file`,
  `read_file` and the others that name a file is refused (policy event `seal`) when the file is one the sandbox seals,
  before anything is weighed, so nothing asks; a read of Dot's own files and of `[policy] sealed_paths` is refused too,
  and a write of what Dot runs. The test's table names the rows it refuses (`SEALED_ROWS`: a write and an edit of Dot's
  own files; its world holds no file that is read-sealed) and fails on any other. This is the one thing such a run no
  longer does. A run the owner started has no sandbox and no seal in yolo, as before.
- **A hand-off past the hop limit refuses nothing in yolo and free mode** (item 1741). Every hand-off from one agent to
  another (a run another run delegated or messaged, a work order's attempt, the lead's run woken by its agent's answer,
  a thing on Dot's list a run put there) is one hop further than the run it came from (`runs.hops`, set by Dot and never
  by a model); a message of a person, a schedule or a watcher starts a chain at 0. Past `[agent] max_hops` (8; `0` turns
  it off) a yolo run, work handed on by a yolo run (to an agent set to ask too), and every run while free mode is on make
  the hand-off all the same: it is logged (`hop_limit_passed`) and the owner is told once for each run that hands off,
  at the first hand-off past the limit, by a notification that opens the run it came from, where Stop is. No question
  goes through the approval broker and nothing waits. Only a run that asks (`ask`, `auto`, `supervised`), handed work
  by a run that asks, is refused past the limit, with one question card on the run it came from; that card is the
  gate's kind of place, reached by no yolo or free run (`GATE_SITES` of the test). The default is above every chain
  measured on the owner's Mac (530 runs, none more than one deep). The cost of a runaway chain in yolo is bounded by the
  owner's own budget setting, not by a refusal.

## Known asks

Found by the test, not changed by it: places that put a question to the owner themselves, past the gate, so that yolo and
free mode do not waive them. Each is named by file and function in `KNOWN_ASKS` of the test, which fails for a new one.

| Tool | When it asks | Where |
| --- | --- | --- |
| `meeting_post` | A task's post in the chat of a meeting Dot is in asks, with its words whole, in every mode, free mode included (item 1138). By design, in [Meetings](meetings.md). | `MeetingDesk.post` |
| `meeting_join` | A join that is not the owner's own word (a run or chat that read outside content, or a chat Dot opened) asks in every mode, free mode included (item 1150). By design, in [Meetings](meetings.md). | `MeetingDesk.join` |
| `meeting_leave` | A run that read outside content asks before it leaves a meeting, in yolo; free mode does not ask. | `MeetingDesk.leave_asked` |
| `budget` | A run past a cap of what it spends that the owner set (`[budget]`: a run with its helpers, the day, a source's day) waits and asks them, **Continue** (as many tokens again) or **Stop**, in every mode, free mode included; no answer within `approval_timeout_s` stops it. The owner's own word of 2026-11-24 (item 1731): a new ask, made by their own setting. The default caps are above everything measured on a busy Mac, so only a runaway reaches them; 0 turns each off; below them nothing asks, and the one cost is a read of the store before a step's calls. In [Limits](limits.md). | `keep_to_budget`, `agent/budget.py` (called by `execute_calls`) |
| `ask_user`, `ask_secret` | The agent's own question. A yolo run leaves it on the chat and goes on, and so does **every** run while free mode is on, whatever its own mode (item 1563: `can_ask_user`, `agent/progress.py`, reads free mode; the stall ladder's `ask` rung comes from the same flag); only a message sent with Plan first, the owner's own word for that run, still asks. `ask_secret` follows the same flag: its card is **left** on the chat (parked) and the run goes on, as in yolo; the card keeps waiting for the owner's value, and what a saved value does for the run that parked it is the ask-a-secret work. Pinned by `test_in_free_mode_the_agents_own_question_is_left_whatever_the_runs_own_mode`, `test_in_free_mode_the_agents_secret_card_is_left_and_the_run_goes_on` and, for the modes that can ask with free mode off, `test_outside_free_mode_a_run_in_an_asking_mode_still_waits_for_its_question`. | `tools/meta.py`, `agent/loop.py` |

## What they record

Each step a question or a refusal was waived for is an ordinary approval record decided by `yolo` (the summary is the
step, the note the reason the policy would have asked), and an `approval_auto` event; no `approval_requested` is sent, so no
screen offers a question. A yolo run also logs each call (`yolo_step`) and keeps `yolo.jsonl` in the run's undo folder.
In free mode a step that carries a secret out of the Mac is recorded and not refused, and a run that starts while it is on
is told so. A run that did what the policy would have asked about can always be read afterwards.

## Who may change it

- **The owner**, and only them: the approval mode in the chat's composer and `[policy] approvals`, free mode in Settings or
  `[policy] free` in their own `config.toml` (never committed). A phone may switch free mode off and never on (item 678).
- The code that decides is under what the review guide says always needs a human: `src/dotagent/policy/`,
  `src/dotagent/agent/approvals.py` and `src/dotagent/agent/review.py` ([REVIEW.md](../REVIEW.md)). The gate that waives,
  `src/dotagent/agent/gate.py`, is not on that list: the test and the rule below are what guard it.
- The contract itself, the exceptions above and `KNOWN_ASKS` of the test, change on the owner's word alone.

## How it is verified

`uv run pytest tests/unit/test_freedom_contract.py` (macOS; the gate runs it with every unit test):

- **One table of calls of every kind of tool**, each prepared by the tool's own `prepare`, so that it follows the tools'
  classification and not a hand-written action: shell (read, write and delete in and outside the workspace, `curl`, `git
  push`, a deploy, mail), files (write and edit anywhere, Dot's own files, a protected place), the browser (navigate,
  click, type, submit a form, a post, a payment, an upload, a vault login), the owner's Safari (the same steps,
  judged as the browser's are, and a password field), the desktop (click, type, open an app,
  AppleScript), the web, MCP, schedules, memory, delegation and teams, the terminal, waits, drafts, the phone,
  `add_proof` and code mode's `code_run`. A proof check (item 1853) is a `shell` call Dot makes for the run, judged as
  that row of the table is, so it adds no ask. A real
  `RunGate` on a real store decides each in every way a run can stand: yolo and free (also over `ask`, `auto` and
  `supervised`, in a chat of Dot's own, after outside content), and yolo alone in each kind of chat. Where this page names an
  ask (a payment, in yolo, in a chat of the owner's or of Dot's own initiative) the owner is asked and nothing else;
  everywhere else the call goes through unasked and a step the policy would have asked about leaves a record saying
  why.
- **Every tool of the registry has a row** or says why it has none, so a tool added later without one fails the test.
- **Every place of the source that asks the owner is named**; a new one fails the test. It reads a call of the approval
  broker, its method taken as a value and `getattr`, and which modules import the broker's class; a call made through a name
  it cannot tie to the broker is for the review to find.
- **A scripted run through the real loop**, with a browser that presses nothing, ends done in free mode with no approval
  event and no `ask_user`, and records why it would have asked; the same run in yolo alone asks only about the payment,
  and records the post as yolo's.
- **The gate adds no wait**: no approval and no sleep is awaited in a run that asks nothing, with no pace set for the site
  (a pace is the owner's own wait, above).
- The exceptions are compared with the engine's own flags, and each refusal above but the desktop's has a test.

[REVIEW.md](../REVIEW.md) makes the review of every push read for it.

## The rule for every contributor

The rule for every contributor: **a change that makes a yolo or free run ask something new, or deny something it
allowed, is a regression.** Before you change a tool, the policy, the gate or the loop, read this page; after, run the
test. A new tool needs a row in the table. A new place that asks the owner is a new ask: it is not added to `KNOWN_ASKS`
without the owner's yes, and the page changes with it.
