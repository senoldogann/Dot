# Agents and campaigns

## Agents

An agent is a profile you make for a kind of work: a name, a role in one line, instructions, and bounds for its runs (a
model, a reasoning level, approvals, the tools it may use; each left out takes the configured one, no tool picked means
every tool) and a look: a shape, a rest expression and a colour of the character (see the design system), picked in the
editor's Look field with a moving preview, worn as a still face wherever the agent shows, and until you choose one the
shape and colour its id's hue gives it (`PUT /api/agents/{id}/look` sets the look alone; Dot's own is the "Dot's look"
card of Settings › General, kept by the daemon for every browser). **Agents** in the sidebar lists Dot first and then
your agents, each with its newest chat; **New agent** opens the editor; an agent's page greets with its face and role,
takes a message for it ("Message Scout") and lists its chats, with Edit and Delete in the bar. The box of that page has
the home's Mode, Model, Reasoning and Options pickers (item 426), starting from the agent's own: its model, its
reasoning level and its approvals, not what this browser has chosen for the home's box; the mode, model, reasoning
level and approvals chosen there go with that message, in place of the agent's own for that run, and leave the home's
choices as they were. Fast mode and goal mode are the home's own, shared with it in this browser: the agent has no
bounds for them, and turning one on there turns it on in the home's box too. The home composer offers an Agent menu once
an agent exists, Dot by default. A run of an agent opens its system prompt with the agent's own
section ("You are Scout, an agent the owner made: ..." and your instructions), ahead of Dot's rules, which still hold;
its tools are the ones the agent names plus finishing, asking you and turning tool groups on, and the tools of an MCP
server you pick for its message, or picked for an earlier message of its chat (item 415): your pick widens the agent's
bounds for that chat; a server the model only called does not. The run's page wears the agent's face and name in the bar
with a chip to its page; its rows in the sidebar and on the home's Today strip wear the face too. A schedule can run
as one of your agents (its **Agent** on Schedules, item 462; see [Memory and schedules](memory-and-schedules.md)).
Deleting an agent leaves its chats, its lessons and its learned skills to Dot, and switches its schedules off ("switched
off: its agent was deleted"), as Dot's with no agent named, so that none goes on firing with every tool; one you switch
on again runs as Dot, or as the agent you then pick in its form. The dialog says how many of them it switches off: the
ones on. The bar of an agent's page names the agent as the page's
heading, as the tab does ("Scout · Agents · Dot"), and Edit opens the editor at the top of the page, not where the
greeting stood. **Pause** in the bar (item 465; `POST /api/agents/<id>/pause`, and `/resume`) keeps the agent's
schedules from starting anything by themselves until **Resume**: the list and the page say "Paused", and its schedules'
rows "<name> is paused" (see [Memory and schedules](memory-and-schedules.md)). What you send the agent, **Run now** of
one of its schedules and the messages of other agents still reach it, and runs already working go on, each with its
Stop. Saving the agent in the editor, or a restart, keeps the pause; Dot is paused by switching its schedules off.

An agent can start from a template (item 315). Under the list, **Start from a template** shows fourteen: Sales
Outbound, Talent Scout, Paid Media, Expense Manager, Product Performance, Bug Reproduction, Account Health, Chief of
Staff, Reviewer, Research Desk, Competitor Watch, Release QA, Copy Editor and Design Critic, each with its face, its
role, what it needs of the Mac and the schedule it suggests. **Use template** makes the agent in one click and opens
its page; it is then an agent like any other, to change in the
editor. Every template names the tools its agent may use (item 425), and its card counts them under **Tools** (a 40 px row to tap on a phone) and lists
the names a click below it: no template starts an agent with every tool, and a tool this home does not have (its group
off in the configuration, or its app not here) is struck through with the reason, since such a template is refused on use. Every
template's instructions also say that what leaves the Mac for a person is a draft for you to send (`draft_message`, see [Drafts](runs.md#drafts)). Sales
Outbound, Talent Scout, Paid Media, Product Performance, Research Desk, Competitor Watch, Release QA and Design Critic
name the web, Dot's Chrome, files, memory, plans, pages and `draft_message`, none of which drives Mail, Messages or a
terminal; a Send pressed on a web page is an external send, under your approvals. Copy Editor names the same without
the web and Chrome. Chief of Staff and Account Health add `apple_script`, the way into Mail and Calendar. Expense
Manager adds it with `shell` for the folders the receipts sit in; Bug Reproduction and Reviewer run things, so they
name the shell and the terminal's tools, and the Reviewer names no web at all. Ten templates suggest a schedule
(item 464): Sales Outbound and Account Health on weekdays at 9:00 and 8:30, Chief of Staff on weekdays at 7:30, Paid
Media every day at 8:00, Talent Scout on Mondays at 10:00, Product Performance and Competitor Watch on Mondays at 9:00
and 8:00, and Expense Manager, Release QA and Reviewer on Fridays at 16:00, 15:00 and 17:00. Their card offers a
switch, **Also add this schedule, switched off until you read its goal**: on, **Use template** makes the agent and a
schedule of it with the template's time (in your time zone) and its goal, the whole goal of one run, which the agent's
instructions then guide; the schedule is added switched off, so nothing runs before you have read what it will do, and
a schedule that could not be added leaves the agent made and says why. An agent's page lists the schedules its runs
run as under **Schedules**, each with its goal in full, when it runs and its switch, and **Edit** on the Schedules
page. An agent's page has
**Export** in its bar: a file
`<name>.dot-agent.json` with the agent's name, role, instructions, model, reasoning level, approvals, tools and look
(`"format": "dot-agent"`, `"version": 1`), never its id, memory, lessons, skills or chats. An agent whose name, role
or instructions hold a secret is not exported, and the page says to take it out first: a value Dot holds (any value
of the home's `.env`, and a key the daemon has read from the Keychain), or a key, a token or a password by its form,
as the masking of secrets finds them. **Import** on the Agents page makes the same agent from such a file, in this home or another, and
opens it; a file that names a tool this home lacks is refused with the tool's name. The API: `GET
/api/agent-templates` (each with `cron` and `task`, empty for one that suggests no schedule), `POST
/api/agent-templates/{id}/use`, `GET /api/agents/{id}/export` and `POST /api/agents/import` (422 for Dot, for a
secret, and for a file that is not an agent's or names tools not here); a template's schedule is `POST /api/schedules`
with `"enabled": false`.

An agent learns for itself. A lesson drawn from an agent's run is the agent's own (the note carries its `agent_id`), and
so is a skill its run was distilled into (`agent_id` in `playbook.json`); Dot's lessons and skills are everyone's. An
agent's run is handed its own lessons first, then Dot's notes, never another agent's, at its start and through
`memory_search` alike; it is offered its own learned skills and Dot's, and another agent's only once you mark that skill
shared (Customize › Skills or the agent's page, `PUT /api/skills/learned/{name}` with `shared`). A skill a run is not
offered stays out of its reach: the `skill` tool reads only one the run is offered, and asked for another by its name,
it says that no skill offered to the run is called so and names only those that are. The scores the agent ranks its
notes by, and the ones its page shows, count its own runs alone. An agent's page lists what it learned ("What it
learned": its lessons with how its runs fared after each, with Forget, and its skills with Share and Drop), and the
Memory page marks a lesson with its agent's name. A note a run saves with `memory_save` is Dot's, for every run.

A run can make a teammate itself (item 99). The `create_agent` tool (a name, a role, instructions, the tools it may
use, a model if one is wanted) makes an agent as the Agents page does, under your approvals (the `persistence` flag,
like saving a note: `ask` asks once the run has read untrusted content, `yolo` makes it); the agent lasts after the
run, its record keeps the run that made it, and the Agents page marks it "Made by Dot". The prompt's Team section
tells a run with no team that when the work splits into roles (finding sources, writing, checking) or into parts
that recur, it makes the teammates it needs, each with only the tools its role needs, and sends them the parts with
`message_agent` (several at once work in parallel); with a team, it reuses a member before it makes one, never for a
single step. An agent a run makes can do no more than the run (item 307): only tools the run has (with none named, an
agent's run gives its own bound and Dot's run every tool), a helper model or the run's own, at most three a run, and a
research run makes none; its runs open with "an agent Dot made" and "Dot's instructions to you", not yours.
`message_agent` is offered whenever `create_agent` is, so a teammate made mid-run can be messaged at once. Every step, a run that may message the team learns of the agents that joined since it started
(made by it, by you or by another run) through a note with their names and roles. Delegated runs and the runs that
answer a message make no agents.

Agents work together through messages. A run (Dot's or an agent's) sends one with the `message_agent` tool, naming the
agent and the message; the agent answers in a run of its own (its instructions, its tools, its model, and the stricter
of its approvals and the sender's, so an agent set to `yolo` asks when a run that asks messages it) that cannot see the
sender's conversation, and the reply comes back as the tool's result, the way a delegated run's report does. The prompt
lists the team by name and role under "Team" whenever there is another agent to message; an agent does not message
itself, and an answering run messages no one further. The sender's chat shows the exchange as a delegated row wearing
the agent's face. **Team** (a button on the Agents page, `#/team`) is today's thread of these messages: who to whom, the
message, the reply, the time and the two runs; an agent's page lists the messages it sent or received. `GET /api/team`
gives the thread. Three `[agent]` settings follow the harness study arXiv 2609.20804: `context_elide_share` (0.6, past
which older tool output is shortened, once, to 700 characters; a step's request to Anthropic also marks the end of the
part of the run the next step sends unchanged, before the newest tool results, as Anthropic looks for a cached prefix
only 20 blocks back from a mark, so the step after a shortening still reads the run before it from the provider's
cache), `context_summarise_share` (0.85 of the configured budget, past which the older part of the run is summarised)
and `plan_each_turn` (on: the open plan is appended to the system message of every step). The API: `GET /api/agents`
(Dot first, each agent with its newest run), `POST /api/agents`, `GET`/`PUT`/`DELETE /api/agents/{id}` (Dot is neither
changed nor deleted; `tools` names the tools the agent may use, or is empty with `every_tool: true` for all of them, and
a request with neither is refused, so a tool list that did not load never lifts an agent's bounds; the editor's Save
waits for the list, and a level the form cannot show is kept as saved), `GET /api/tools` (the names an agent may be
given), and `POST /api/runs` with `agent_id` (not with
`standing`: a campaign is Dot's own, and the two together are refused with a 422).

## Campaigns

```bash
dotx run --standing "Collect 50 leads of Berlin design studios with a name and an e-mail address into ~/leads.csv"
dotx campaign list
dotx campaign show ID
dotx campaign stop|resume|achieved ID
```

A campaign is a task that Dot keeps working on until its outcome is verified or you stop it. Switch on **Keep going** in
the Execution group of the composer's **Options** menu (it is there for a task of Dot's), or pass `--standing` to `dotx
run`. The run you start is its first session, in your conversation; the daemon runs the sessions after it, each a run in
a thread of its own, through restarts and days. A campaign whose first session cannot start is not kept.

- **Targets.** The first session records the campaign's targets and how a session verifies them (`campaign_targets`). A
  session that wants to end while the campaign has no targets is sent back once to record them; targets a later session
  replaces stay in the ledger.
- **The ledger and the brief.** Each session writes what it did, with numbers and evidence, in the campaign's ledger
  (`campaign_note`: progress, setback, strategy, question or note). The next session opens with a brief instead of a
  conversation: the goal, the targets, the paths of the files you attached to the goal, what you wrote in the campaign's
  conversation since it started, the newest strategy and progress, the last 12 entries of the ledger, the questions left
  for you and what became of them, the steps kept for you (below) and what became of them, and how the last session
  ended. The prompt of a session says to write its progress there, not in the notes of the memory, and what you tell it
  while it works too. The targets, the ledger and the plan of the next session shape every session after, so writing
  them carries the `persistence` flag, as saving a memory note does: once a session has read untrusted content, the
  policy asks first.
- **The next session** starts `campaign_gap_minutes` (60) after the last one ended, unless a session planned it with
  `campaign_schedule` (a moment, after which a rhythm goes on, or a cron rhythm). A session that ended needing you, with
  questions it left for you and none answered yet, or one that kept a step for you still unanswered, whatever it said it
  needs, is followed after `campaign_wait_hours` (24) instead; your answer, or your skip, starts the next session right
  away, and its brief carries it (while the session that asked works in the daemon, your answer goes to it). With
  `[policy] unanswered = "park"` a session keeps for you, as any run does (item 484,
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)), a step handed to you or a question for
  your yes that nobody answered: it is the campaign's, kept once for all its sessions, in its conversation; I did it,
  Allow once or Skip reach the session while it works, and otherwise bring the next session forward with no run of their
  own, its brief saying what you answered ("allowed by the owner once: do it when you reach it, exactly as it was,
  once"), so that a later session may do an allowed step once, and none tries a skipped one again until you write in the
  campaign's conversation. After `campaign_patience_sessions` (3) sessions in a row without a progress entry, the brief
  opens the next session with a change of route: it lists the strategies written since the newest progress, which are
  refused (`campaign_note` does not take one of them again while the campaign is stuck), and asks for another channel (a
  site's API, a command or a skill instead of its pages), another source or another approach, written as a new strategy
  entry first. All four live in `[agent]`.
- **Only a verified outcome ends it.** `campaign_achieved` asks the goal checker (the review model) whether the targets
are reached, from the targets and how they are verified, the progress in the ledger, the session's plan with the
evidence of its steps, what the session's own calls measured and the session's evidence. Refused, the call fails with
the checker's reason and the session goes on (one the checker finds out of reach asks you how to go on); accepted, the
campaign is achieved and you are told (the notifications under the web UI and the phone's pushes), as you are when the
emergency stop or `campaign_max_sessions` stops it.
- **Only you stop it.** Campaigns, in the sidebar, lists them, and a row has buttons for what you can do to its campaign
  without opening it: Stop while it is active, Mark achieved until it is achieved, and Delete once it is not active and
  none of its sessions works; each asks first. A campaign's page shows its targets, the questions waiting for you, its
  ledger and its sessions, with Stop (which also stops a session working then), Go on, Mark achieved and Delete. The
  emergency stop (its button, or the pointer in the top-left corner during a desktop step) stops the campaign of the
  session it ends, and while the stop is engaged no session starts. `dotx campaign` changes the campaign without the
  daemon, so a session working then goes on until it ends (Stop on its run page ends it). `campaign_max_sessions` (0: no
  limit) stops a campaign after that many sessions; Go on gives it one more.
- **Deleting one** (`DELETE /api/campaigns/{id}`) deletes its ledger and the threads of its sessions after the first,
  with all they hold, as deleting those chats would. The conversation it started in stays a chat, with the first
  session as a run of that chat, and the questions its runs left for you expire, as no session reads the answers any
  more. It is refused while the campaign is active, so stop it first, and while a run works in one of its chats.
- **Posts, judged before they are answered.** With `TYPESAFE_API_KEY` saved, a session also has three tools on
  TypeSafe's typed judgments for a campaign on X or another feed. `rank_posts` asks three yes/no questions about every
  post it could answer, in one request (on the account's topic; written by a person, not a bot, a brand or spam; worth a
  reply that adds something) and combines them with weights the ledger holds: the first ranking writes them as a note
  with equal values, and a `campaign_note` entry with the numbers `w_topic`, `w_person` and `w_reply` changes them
  without asking TypeSafe again; every ranking's scores are kept in the session's timeline (`posts_ranked`).
  `pick_draft` asks one choice over two to ten drafts of a reply. `check_draft` asks whether a draft is off topic,
  repeats one of the account's earlier posts or breaks a rule the goal names, and refuses it when one of those reaches
  `[judge] draft_check_threshold` (0.5, provisional until it is set on a labeled sample of your own feed). None of them
  posts; the model keeps the decision.

**The browser of a campaign.** A campaign works in the Chrome you have open, signed in as you are (`attach`), unless you
start it with `--browser own` (or `browser` in the API, which goes with `standing` only), and `[agent] campaign_browser`
sets the default; runs you start by hand keep `[browser] mode`. A campaign that asks for your Chrome is refused at its
start while that Chrome is closed or does not allow the connection (`dotx browser attach` sets it up), so its sessions
do not fail one after the other. The brief of every session says which Chrome it works in, and so do
`dotx campaign show` and the campaign's page.

Every session runs with the model, reasoning level and approval mode the campaign started with, and without goal mode:
the campaign's own check takes its place, so starting one with goal mode on is refused. A run's limits
(`run_max_minutes`, `run_max_tokens`) apply to each session. Sessions run only while the daemon runs; one the daemon cut
off is not resumed as a run: the next session starts once the daemon is back (after `campaign_gap_minutes` when the
daemon died while it worked, so that a session that brings it down does not run again and again), and its brief says the
last one was cut off. A session whose `dotx` command died (the first one, from `dotx run --standing`) is closed by the
daemon within half a minute, and the next one starts `campaign_gap_minutes` after that. The threads of the sessions
after the first are not in the chat list; the campaign's page links them. What you write in the campaign's conversation,
and your answers to their questions, are instructions of the sessions after it.
