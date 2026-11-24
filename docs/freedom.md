# The freedom contract

The owner's word of 2026-11-24: improve Dot, but never break its freedom, **yolo mode and free mode**, and make computer
use, browser use and the other autonomous work better and faster. This page is that promise in one place: what the two
modes guarantee, the exact exceptions and the code that makes each, what they record, who may change them and how a
change that breaks them is caught. [Autonomy](autonomy.md) and [Safety](safety.md) describe the modes; this page is the
contract, and `tests/unit/test_freedom_contract.py` is what makes it executable.

## The guarantee

**A run in yolo, and every run while free mode is on, asks the owner nothing, and every tool action runs unasked.**

- **Yolo** (`approvals = "yolo"` of the run; the chat's pill "Pay or send", item 443): the gate waives every question and
  every hand-off, each with the reason it would have asked for: the question that outside content raises, the one about
  Dot's own files (`_OWN_FILES_REASON`), money and credentials, a login filled from a vault (`_VAULT_REASON`), a message
  or a post in the owner's name, a pre-approved spend, the preset's and the owner's `ask` rules', and the refusal of a
  read of their secrets or of data sent out; all but the exceptions of the first two rows of the table below. The step
  runs and its record says what it would have asked.
- **Free mode** (`[policy] free = true`, Settings › Rules › How Dot asks, `PUT /api/policy/free`, item 922): the same
  for every run of every chat, whatever its own approval mode, from its next step on, and the payment, the message or
  mail sent for the owner, the public post and the release too (the gate's `held` is empty while it is on).

**Freedom costs no speed.** The gate of such a run is one policy decision and one record; it awaits no answer and no kept
step (the pace the owner set for a site, which spaces the public actions of the browser, is the one wait left, and is
theirs), and a call's `tool_result` event keeps `waited_s`, the time it spent at the gate. A change that puts a wait in
that path breaks the contract as an ask does.

## The exceptions, with the code that makes each

What still waits for the owner's yes. Nothing else does.

| Where | What asks | The code |
| --- | --- | --- |
| Yolo, a chat the owner opened (item 860) | a payment (handed to them, never answered by a model), a message or mail sent for them, a public post | `owners_guard`, `OWNERS_REASON` and `_OWNERS_FLAGS` (`financial`, `message_send`, `public_post`) in `policy/engine.py`; `held` in `RunGate._decide_spent` |
| A chat Dot opened on its own initiative, in every mode but free (item 850) | a message, a post, a release | `initiative_guard`, `_INITIATIVE_REASON` and `_INITIATIVE_FLAGS` in `policy/engine.py` |
| Yolo, a schedule's run in the owner's chat (nobody is there) | a message or a post is judged by the review model, and what it cannot tell goes to the owner; a payment is always theirs | `RunGate._decide_spent` (`judged`) |

Free mode keeps neither of the first two. What it keeps is below, as known asks: they are not decisions of the gate.

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

## Known asks

Found by the test, not changed by it: places that put a question to the owner themselves, past the gate, so that yolo and
free mode do not waive them. Each is named by file and function in `KNOWN_ASKS` of the test, which fails for a new one.

| Tool | When it asks | Where |
| --- | --- | --- |
| `meeting_post` | A task's post in the chat of a meeting Dot is in asks, with its words whole, in every mode, free mode included (item 1138). By design, in [Meetings](meetings.md). | `MeetingDesk.post` |
| `meeting_join` | A join that is not the owner's own word (a run or chat that read outside content, or a chat Dot opened) asks in every mode, free mode included (item 1150). By design, in [Meetings](meetings.md). | `MeetingDesk.join` |
| `meeting_leave` | A run that read outside content asks before it leaves a meeting, in yolo; free mode does not ask. | `MeetingDesk.leave_asked` |
| `ask_user`, `ask_secret` | The agent's own question. A yolo run leaves it on the chat and goes on. In free mode a run whose own mode asks may **wait** for the answer: the loop reads the run's own mode (`can_ask_user`, `agent/progress.py`), not free mode, and the stall ladder's `ask` rung comes from the same flag. A gap between the owner's word and the code, pinned by `test_known_ask_in_free_mode_the_agents_own_question_waits_when_the_run_asks_in_its_own_mode`. | `tools/meta.py`, `agent/loop.py` |

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
  click, type, submit a form, a post, a payment, an upload, a vault login), the desktop (click, type, open an app,
  AppleScript), the web, MCP, schedules, memory, delegation and teams, the terminal, waits, drafts, the phone and
  code mode's `code_run`. A real
  `RunGate` on a real store decides each in every way a run can stand: yolo and free (also over `ask`, `auto` and
  `supervised`, in a chat of Dot's own, after outside content), and yolo alone in each kind of chat. Where this page names an
  ask the owner is asked and nothing else; everywhere else the call goes through unasked and a step the policy would have
  asked about leaves a record saying why.
- **Every tool of the registry has a row** or says why it has none, so a tool added later without one fails the test.
- **Every place of the source that asks the owner is named**; a new one fails the test. It reads a call of the approval
  broker, its method taken as a value and `getattr`, and which modules import the broker's class; a call made through a name
  it cannot tie to the broker is for the review to find.
- **A scripted run through the real loop**, with a browser that presses nothing, ends done in free mode with no approval
  event and no `ask_user`, and records why it would have asked; the same run in yolo alone asks only about the post and the
  payment.
- **The gate adds no wait**: no approval and no sleep is awaited in a run that asks nothing, with no pace set for the site
  (a pace is the owner's own wait, above).
- The exceptions are compared with the engine's own flags, and each refusal above but the desktop's has a test.

[REVIEW.md](../REVIEW.md) makes the review of every push read for it.

## The rule for every contributor

The rule for every contributor: **a change that makes a yolo or free run ask something new, or deny something it
allowed, is a regression.** Before you change a tool, the policy, the gate or the loop, read this page; after, run the
test. A new tool needs a row in the table. A new place that asks the owner is a new ask: it is not added to `KNOWN_ASKS`
without the owner's yes, and the page changes with it.
