# Agents and campaigns

## Agents

An agent is a profile you make for a kind of work: a name, a role in one line, instructions, and bounds for its runs (a
model, a reasoning level, approvals, the actions it may use (its tools); each left out takes the configured one, and **All actions**,
over the actions' boxes, means every tool, any added later too: an agent with every tool opens with every box ticked,
every box ticked saves as every tool, an agent that names each tool there is keeps them named through a save until you
tick All actions yourself, and none ticked is refused, item 512) and a look: a shape, a rest expression and a colour of
the character (see the design system), picked in the editor's Look field with a moving preview, worn as a still face
wherever the agent shows, and until you choose one the shape and colour its id's hue gives it (`PUT
/api/agents/{id}/look` sets the look alone; Dot's own is the "Dot's look" card of Settings › General, kept by the daemon
for every browser). **Agents** in the sidebar lists Dot first and then your agents, each with its newest chat; **New
agent** opens the editor; an agent's page greets with its face and role, takes a message for it ("Message Scout") and
lists its chats, with Edit and Delete in the bar. The box of that page has the home's Mode, Model, Reasoning and Options
pickers (item 426), starting from the agent's own: its model, its reasoning level and its approvals, not what this
browser has chosen for the home's box; the mode starts on Auto, as the home's does, the message's words settling it, and
`/auto`, `/task`, `/chat` and `/research` name one (item 513); the mode, model, reasoning level and approvals chosen
there go with that message, in place of the agent's own for that run, and leave the home's choices as they were. Fast
mode and goal mode are the home's own, shared with it in this browser: the agent has no bounds for them, and turning one
on there turns it on in the home's box too. The home composer offers an Agent menu once an agent exists, Dot by default.
A run of an agent opens its system prompt with the agent's own section ("You are Scout, an agent the owner made: ..."
and your instructions), ahead of Dot's rules, which still hold; its tools are the ones the agent names plus finishing,
asking you and turning tool groups on, and the tools of an MCP server you pick for its message, or picked for an earlier
message of its chat (item 415): your pick widens the agent's bounds for that chat; a server the model only called does
not. A run it delegates a part to (`delegate`) is bound to the agent's tools too, without the servers picked for a
message, and carries the agent's id (item 553). Ask the delegated run to end with a `Verification:` section: the
command or check it ran and the answer. A report without one is marked as a claim, so the sender does not close a plan
step on trust alone. The run's page wears the agent's face and name in the bar with a chip to its page; its rows in the sidebar and on
the home's Today strip wear the face too. A schedule can run as one of your agents (its **Agent** on Schedules, item
462; see [Memory and schedules](memory-and-schedules.md)). Deleting an agent leaves its chats, its lessons and its
learned skills to Dot, and switches its schedules off ("switched off: its agent was deleted"), as Dot's with no agent
named, so that none goes on firing with every tool; one you switch on again runs as Dot, or as the agent you then pick
in its form. The dialog says how many of them it switches off: the ones on. The bar of an agent's page names the agent
as the page's heading, as the tab does ("Scout · Agents · Dot"), and Edit opens the editor at the top of the page, not
where the greeting stood. **Pause** in the bar (item 465; `POST /api/agents/<id>/pause`, and `/resume`) keeps the
agent's schedules from starting anything by themselves until **Resume**: the list and the page say "Paused", and its
schedules' rows "<name> is paused" (see [Memory and schedules](memory-and-schedules.md)). What you send the agent, **Run
now** of one of its schedules and the messages of other agents still reach it, and runs already working go on, each with
its Stop. Saving the agent in the editor, or a restart, keeps the pause; Dot is paused by switching its schedules off.

The page is in three parts (items 509 and 610): **Your teams** and **Your agents**, each with its count, and under
them **Add ready-made agents**, which shows the teams or the single agents, one of the two at a time (**Teams** first;
the one chosen last while the app is open). Your agents are a grid of compact cards, as many across as the page has
room for, two to four, and one on a phone: the face, the name over the role, each on one line ("Made by Dot" and
"Paused" go under a name too long to share its line, item 782), and under them the title of the agent's newest chat
(the chat's own, as you may have renamed it) with how long ago it began, or that it has none. Your teams are wider
cards, two across at most, each as wide as two of the agents' columns, so that the two grids end at the same edge. Dot's card names Dot's newest chat of its own (item 633): one that is not an agent's, a campaign's or a team's,
which their own cards name. How long ago keeps up with the clock while the page stays open, and the list is read again
when a run starts or ends. The card is the way to the agent's page, and says "Paused" and "Made by Dot" as the page
does; Dot's opens the chats.

An agent can start from a template (item 315). **Single agents** shows fifteen: Sales Outbound, Talent Scout, Paid
Media, Expense Manager, Product Performance, Bug Reproduction, Account Health, Chief of Staff, Reviewer, Research Desk,
Competitor Watch, Release QA, Copy Editor, Design Critic and Trader (item 847: the trading desk's agent, whose card
opens the desk and whose tools come with trading on; see [Settings › Trading](daemon.md#settings--trading)), each with
its face, its role, what it needs of the Mac and
the schedule it suggests. **Use template** makes the agent and opens its page; it is then an agent like any other, to
change in the editor. Eleven templates first ask what their agent's first run would otherwise have to ask you (item
876), three questions at most (Sales Outbound: whom you sell to, what you offer them, where; Release QA: which
address, which test account by its name, which steps must always work): your answers go at the end of the agent's
instructions, each after its question, under "What the owner told you when making you"; a blank one is left out, and
**Skip** makes the agent as the template has it. An answer that holds a secret is refused, as in an agent's file (a
password is asked for when it is needed). Paid Media, Expense Manager, Design Critic and Trader ask nothing and make
the agent in one click. Every template names the tools its agent may use (item 425), and its card counts them
under **Actions** (a 40 px row to tap on a phone) and lists the names a click below it: no template starts an agent with
every tool, and a tool this home does not have (its group off in the configuration, or its app not here) is struck
through with the reason, since such a template is refused on use. Every template's instructions also say that what
leaves the Mac for a person is a draft for you to send (`draft_message`, see [Drafts](runs.md#drafts)). Sales Outbound,
Talent Scout, Paid Media, Product Performance, Research Desk, Competitor Watch, Release QA and Design Critic name the
web, Dot's Chrome, files, memory, plans, pages and `draft_message`, none of which drives Mail, Messages or a terminal; a
Send pressed on a web page is an external send, under your approvals. Copy Editor names the same without the web and
Chrome. Chief of Staff and Account Health add `apple_script`, the way into Mail and Calendar. Expense Manager adds it
with `shell` for the folders the receipts sit in; Bug Reproduction and Reviewer run things, so they name the shell and
the terminal's tools, and the Reviewer names no web at all. Ten templates suggest a schedule (item 464): Sales Outbound
and Account Health on weekdays at 9:00 and 8:30, Chief of Staff on weekdays at 7:30, Paid Media every day at 8:00,
Talent Scout on Mondays at 10:00, Product Performance and Competitor Watch on Mondays at 9:00 and 8:00, and Expense
Manager, Release QA and Reviewer on Fridays at 16:00, 15:00 and 17:00. Their card offers a switch, **Also add its
schedule; you switch it on on the Schedules page after reading its goal**: on, **Use template** makes the agent and a
schedule of it with the template's time (in your time zone) and its goal, the whole goal of one run, which the agent's
instructions then guide; the schedule is added switched off, so nothing runs before you have read what it will do, and a
schedule that could not be added leaves the agent made and says why. The switch is for that one Use, not a setting: the
card is drawn with it off again. An agent's page lists the schedules its runs run as under **Schedules**, each with its
goal in full, when it runs and its switch, and **Edit** on the Schedules page; an agent with the name of a template that
suggests a schedule, and without that schedule (made before, kept by Use team, or its schedule deleted), is offered it
there, **Suggested by its template** with **Add it**, which adds it switched off (item 722). An agent's page has
**Export** in its bar: a file `<name>.dot-agent.json` with the agent's name, role, instructions, what it never does,
model, reasoning level, approvals, tools and look (`"format": "dot-agent"`, `"version": 1`), never its id, memory, lessons, skills or chats. An
agent whose name, role or instructions hold a secret is not exported, and the page says to take it out first: a value
Dot holds (any value of the home's `.env`, and a key the daemon has read from the Keychain), or a key, a token or a
password by its form, as the masking of secrets finds them. **Import** on the Agents page makes the same agent from such
a file, in this home or another, and opens it; a file that names a tool this home lacks is refused with the tool's name.
The API: `GET /api/agent-templates` (each with `cron` and `task`, empty for one that suggests no schedule, and its
`questions`), `POST /api/agent-templates/{id}/use` (with `answers`, in the questions' order, 500 characters each), `GET /api/agents/{id}/export` and `POST /api/agents/import` (422 for Dot, for a secret,
and for a file that is not an agent's or names tools not here); a template's schedule is `POST /api/schedules` with
`"enabled": false`.

**Teams** (item 504) shows four teams, each a few templates whose agents work side by side: the Content team (Research
Desk, Copy Editor), the Engineering team (Bug Reproduction, Reviewer, Release QA), the Growth team (Competitor Watch,
Paid Media, Product Performance, Sales Outbound) and the Office team (Chief of Staff, Expense Manager). Each card says
what the team does and shows the faces and names of its agents, those you have already marked **Yours** (item 512).
**Use team** makes, in one click, each agent of the team you do not have yet, as its template's **Use template** makes
it. An agent of yours with the name of one of them, in any case, is that agent, kept as it is but for one thing: one
that names its tools without `message_agent` is given it, so that it can hand its teammates their parts, and the toast
says so; a team one of whose agents this home cannot make (a tool it lacks) makes none of them and says why. It then
makes the team itself, a record under **Your teams** (item 610, below); used again, it finds that record, whatever you
have named it since, and brings back an agent it lacks (one you took out of the team, or deleted) at its end, and the
toast says which; the card of a ready team you have says **In your teams**. A team whose agents suggest schedules offers
**Also add their schedules**, switched off, for every agent of the team: the ones it makes and the ones of yours it
keeps (item 722: those were left without, and the switch read as if it had not taken), but for one that has that
schedule already (its agent and its goal), so that a second Use adds none twice; the toast says how many were added and
which agents had theirs. Every template names `message_agent` beside
`delegate`, and a member's run is told its team (item 512): the prompt's Team section says "You are in the Growth team",
lists its teammates first, each agent with the team it is in, and says that a part an agent's role fits goes to that
agent with `message_agent`, not to `delegate`, which is for a part no agent's role covers. An agent is in a team when
its name is a member's, read as Use team reads it (`team_of`), whoever made it. The API: `GET /api/agent-teams` and
`POST /api/agent-teams/{id}/use` (201 with the agents it `made`, the ones of yours it `kept` as they are now, and the
names of those it `joined` by giving them `message_agent`, and the team (`team`) with `created` for whether it was made
now and `added` for the agents it put back; 404 for a team that is not there, 422 for one that cannot be made).

**Your teams** (item 610) are the teams you have, each a record of a name, what it is for (500 characters at most) and
agents of yours in an order, which holds nothing else: removing a team keeps its agents and its chats, and an agent that
is deleted leaves the teams that had it. Use team makes one from a ready team, **New team** on the Agents page puts
agents of yours together (at least one, each once; Dot is no agent to put in a team), **Select** at the head of Your
agents turns their cards into boxes to tick (item 867), and the bar under them counts the picked ones and opens that
form with them ticked, first, in the order picked (**Make a team**; Cancel leaves the cards as links), and **Edit** in
the bar of a
team's page changes its name, purpose and agents (the agents it has first, in its order, then the others; Save writes
the ticked ones in that order); **Remove** takes the team away and nothing it names. A team is a card with its agents'
faces over one another (four, the rest counted), its name and purpose, how many agents it has and its newest chat with
how long ago it was busy; the card opens the team's page, `#/teams/<id>`: the agents' faces over the team's name and
what it is for, its agents by name, each a link to its page, a box for a message to the team ("Message Content team",
which starts as an agent's box does, on Auto, with Dot's model and approvals) and the team's chats. A team that has no
agent left says so on its page and its card, and takes no message: its box is not there, and the daemon refuses a new
chat with it (422) until you give it an agent.

A chat with a team works as the team. A message from the team's page starts a run of Dot's, with Dot's tools, approvals
and policy, in a new chat that names the team (`POST /api/runs` with `team_id`, which goes with a new chat alone: not
with a thread, an agent or a campaign), and every run of that chat is told at the head of its prompt, whoever starts it,
a follow-up in the chat's box, a queued message, a resumed run or a firing of the chat's loop: that it leads the team
"Content team", the agents working together, and that every message in the chat is for the team; what you made the team
for, which it follows within the rules as it follows an agent's instructions; the team's agents by name and role; and
that the chat is a group chat (item 831, after the owner's screenshots of another agent app): it answers your message
first with one short message to the group, the plan and who does what, naming each agent as @Name, and in that same
reply sends every agent the plan names its part with `message_agent`, all at once (as many at once as the team has
agents, up to eight, rather than `[agent] parallel_tools`, for a reply whose calls all send parts), does no agent's part
itself, and ends with one short message to you: what was decided, what comes next and what only you can decide. Each
agent of the team it sends a part reads, before the part, who is in the chat and that its answer is its message there,
and after it the chat's conversation so far (your messages, the lead's and the agents' answers, the newest 6,000
characters, fenced as text in which what an agent quoted from a page or a mail asks nothing of it), and is asked to
answer as people in a chat do: its part only, short, naming another agent as @Name where their parts meet; its run's
goal, which its row shows, is the part alone. An agent of yours outside the team that the lead sends a part reads the
part alone, as from any run: it is not in the chat. The text is made as each run starts, from the team as it is then, so
an agent taken out of the team or deleted is not named, a team with none says there is nobody to hand a part to, and a
run that has no `message_agent` is told to do the work itself. It is your own configuration, written by you into the
prompt as an agent's instructions are, and it adds no permission and no tool: the agents a run messages answer in runs
of their own, within their own tools and approvals (above). The one thing in it a model may have written is the role of
an agent a run of Dot's made, which the line marks "made by Dot". An agent you message in the team's chat, with
`agent_id`, is that agent's run and does not lead the team. Removing the team leaves its chats as chats with Dot, whose
later messages are told no team.

The chat wears the team: the sidebar's row shows the faces of two of its agents and the team's name ahead of its last
words while the chat's latest run is Dot's, which leads the team, and the bar of a run of Dot's there (a phone's, under
the chat's title) the name and the same faces; an agent you put in the chat answers as itself, its run's bar and, while
its run is the latest, the row wearing its face. The Task tab's Details name the team, a link to its page. The API: `GET
/api/teams` (each team with its agents as they are now and its
newest chat), `GET /api/teams/{id}` (with its chats), `POST /api/teams` (201; `name`, `purpose` and `agents`, the ids of
your agents in order; 422 for an id that is not one of your agents, one named twice, or none), `PUT /api/teams/{id}`
(the same, whole), `DELETE /api/teams/{id}` (204), and 404 for a team that is not there. `GET /api/agents` gives each
agent the title of the chat of its newest run (`latest_title`), and Dot the latest run of its newest chat (item 633); a
chat of `GET /api/threads` and the run of `GET /api/runs/{id}` name the team they are with (`team`: its id, name and
agents; the chat's `actor_id` is the team's id, which no agent's or campaign's is).

**Never does** (item 875), in the editor, is what the agent never does, a line each: the edges of its job ("Work
inbound leads", "Send a message itself"). Knowing what an agent will not do is what makes it safe to leave running, so
they are a field of their own rather than words lost in its instructions. Its runs read them after its instructions,
as lines to stop at whoever asks (a message, a page, a teammate or a tool's output), saying so in the answer instead.
Every template carries its own, which its card lists under **Never does** a click below its actions, and **Use
template** gives them to the agent. `create_agent` takes them too (`refuses`, eight lines at most), so an agent Dot
makes has its edges as well. They go out in the agent's file (`refuses`; a file made before has none) and are checked
for secrets as its instructions are. The API: an agent's `refuses` (2,000 characters at most), which a change that
does not name it keeps.

**Its own folder** (item 877): an agent's run is told its folder, `agents/<its id>/` in the workspace, made when
the run starts, and to read what its last run left there before it starts and keep there what the next one needs (the
files its instructions name, what was done, for whom, what is left), never a secret. The folder goes by the agent's id,
so renaming the agent keeps it and two agents of one template never share a file. The templates keep their files there
(Sales Outbound's `prospects.csv`, Research Desk's `research-<topic>.md`, Release QA's last walk). A teammate's folder
is not named to another agent's run; it is a folder of the workspace, not a wall: the file tools reach it as any other.

**What it used** (item 874), on an agent's page, reads its newest twenty runs that ended: how many, the tokens
they spent and the actions they called most, each with its count. Once three of them have ended (one run says little
of what a role needs), it names the actions the agent may use that none of them called, but for the ones every agent's
run has (finishing, asking) and `message_agent`, its way to its team, with **Remove them**: on your yes they go, and
Edit gives any of them back. An agent with every action is offered **Keep only these**: the actions its runs called
that are tools here, and `message_agent`. An action a role never needs is a way for the agent to go astray, so the
page says which. The API: `GET /api/agents/{id}` gives `usage` (`window`, `runs`, `tokens`, `newest`, `calls`,
`enough`, `unused`), and `POST /api/agents/{id}/trim-tools` takes them away (422 before three runs ended, and when
nothing would go or nothing would be left).

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
like saving a note: `ask` asks once the run has read untrusted content, `yolo` makes it, though once the run has read
untrusted content the review model checks the step first and may refuse it, item 525); the agent lasts after the
run, its record keeps the run that made it, and the Agents page marks it "Made by Dot". The prompt's Team section
tells a run with no team that when the work splits into roles (finding sources, writing, checking) or into parts
that recur, it makes the teammates it needs, each with one job and only the tools its role needs, named one by one, and sends them the parts with
`message_agent` (several at once work in parallel); with a team, it reuses a member before it makes one, never for a
single step. An agent a run makes can do no more than the run (item 307): only tools the run has, and only the ones
it names: one that names none is refused, so that no agent a run makes gets every tool (item 873, after an agent
library whose rule is one job and no leftover tool: four agents Dot made before it had every tool), a helper model or the run's own, at most six a run (three
before item 864), and a research run makes none; its runs open with "an agent Dot made" and "Dot's instructions to
you", not yours. Asked for a team, a run makes its agents, then the team of them with `create_team` (item 864: a name,
what it is for, two to twelve agents by name, yours or ones it made). The team is yours, as one you put together on the
Agents page is: it lists under Teams and in the sidebar, where you start a chat with it. A run makes at most two
teams; a team of an agent nobody made, of one agent, or under a name a team has already is refused, and nothing is
made.
`message_agent` is offered whenever `create_agent` is, so a teammate made mid-run can be messaged at once. Every step, a run that may message the team learns of the agents that joined since it started
(made by it, by you or by another run) through a note with their names and roles. Delegated runs and the runs that
answer a message make no agents.

Agents work together through messages. A run (Dot's or an agent's) sends one with the `message_agent` tool, naming the
agent and the message; the agent answers in a run of its own (its instructions, its tools, its model, and the stricter
of its approvals and the sender's, so an agent set to `yolo` asks when a run that asks messages it) that cannot see the
sender's conversation, and the reply comes back as the tool's result, the way a delegated run's report does. The prompt
lists the team by name and role under "Team" whenever there is another agent to message; an agent does not message
itself, and an answering run messages no one further. The sender's chat shows the exchange as a delegated row wearing
the agent's face: a line of the conversation, no card, its face in the column of the faces and its words where theirs
start (item 884). While the message is on its way, the conversation shows nothing of it: only the main agent's work is
told there, in the line of what it is doing ("Asking Scout…"), and the agent's row stands for the part once its run
starts (item 892, the owner's word; items 668 and 884 had a "Sending to Scout…" line, which stayed in the chat when the
row's name was not the one the message used). The agent's answer then stands in the conversation as its message, with **Every step**
folded under one line (item 733): "Message
from Scout" with its face, which the next agent's answer joins ("Messages from Scout and Editor" and how many), so that
Dot's own answer keeps the conversation; a tap opens it, each message as wide as Dot's answers, the agent's face and
name over its words, cut at 1,200 characters with **Read it all** leading to its run's page. Only the
answer of a run that ended done is shown so; it stays in sight when the chat shows only what Dot is doing (item 663),
folded there too until you open it (item 859: item 830 had it open, and each return to the chat opened it again); one
you opened stays open in that tab when the chat is drawn again.
The run that answers a message names the agent in its `run_queued` event (`agent_name`), which the page reads; a
helper an agent's own run delegated to carries the agent's id (item 553) but no name, and is a helper. **Handoffs** (a button on the Agents page, `#/team`) lists today's messages: who to whom, the
message and the reply (their words, with no markdown marks: item 778), the time and the two runs; an agent's page lists the messages it sent or received. `GET /api/team`
gives the thread. Three `[agent]` settings follow the harness study arXiv 2609.20804: `context_elide_share` (0.6, past
which older tool output is shortened, once, to 700 characters; a step's request to Anthropic also marks the end of the
part of the run the next step sends unchanged, before the newest tool results, as Anthropic looks for a cached prefix
only 20 blocks back from a mark, so the step after a shortening still reads the run before it from the provider's
cache), `context_summarise_share` (0.85 of the configured budget, past which the older part of the run is summarised)
and `plan_each_turn` (on: the open plan is handed as a note after the conversation whenever it changes and after
20 steps without a change, and its closing is said; item 825: it stood at the end of the system message, so that each
change of it had the provider read the run's whole history again uncached). The API: `GET /api/agents`
(Dot first, each with its newest run), `POST /api/agents`, `GET`/`PUT`/`DELETE /api/agents/{id}` (Dot is neither
changed nor deleted; `tools` names the tools the agent may use, or is empty with `every_tool: true` for all of them, and
a request with neither is refused, so a tool list that did not load never lifts an agent's bounds; the editor's Save
waits for the list, and a level the form cannot show is kept as saved), `GET /api/tools` (the names an agent may be
given, read at each request: a tool of an app connected after Dot started is among them, item 689), and `POST /api/runs` with `agent_id` (not with
`standing`: a campaign is Dot's own, and the two together are refused with a 422).

## Campaigns

```bash
dotx run --standing "Collect 50 leads of Berlin design studios with a name and an e-mail address into ~/leads.csv"
dotx campaign list
dotx campaign show ID
dotx campaign stop|resume|achieved ID
```

A campaign is a task that Dot keeps working on until its outcome is verified or you stop it. Switch on **Keep going** in
the **How Dot works** group of the message box's **Options** menu (it is there for a task of Dot's), or pass `--standing` to `dotx
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
  questions it left for you and none answered yet, or while a step kept for you in the campaign is still unanswered,
  whatever the session said it needs, is followed after `campaign_wait_hours` (24) instead; your answer, or your skip,
  starts the next session right away, and its brief carries it (while the session that asked works in the daemon, your
  answer goes to it). With `[policy] unanswered = "park"` a session keeps for you, as any run does (item 484,
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)), a step handed to you or a question for
  your yes that nobody answered: it is the campaign's, kept once for all its sessions, in its conversation; I did it and
  Allow once reach the session while it works, or the campaign's session at work then, which found the step waiting, and
  otherwise, as Skip does, bring the next session forward with no run of their own, its brief saying what you answered
  ("allowed by the owner once: do it when you reach it, exactly as it was, once"), so that a later session may do an
  allowed step once, and none tries a skipped one again until you write in the campaign's conversation or to a session
  at work. After `campaign_patience_sessions` (3) sessions in a row without a progress entry, the brief opens the next
  session with a change of route: it lists the strategies written since the newest progress, which are refused
  (`campaign_note` does not take one of them again while the campaign is stuck), and asks for another channel (a site's
  API, a command or a skill instead of its pages), another source or another approach, written as a new strategy entry
  first. All four live in `[agent]`.
- **Only a verified outcome ends it.** `campaign_achieved` asks the goal checker (the review model) whether the targets
are reached, from the targets and how they are verified, the progress in the ledger, the session's plan with the
evidence of its steps, what the session's own calls measured and the session's evidence. Refused, the call fails with
the checker's reason and the session goes on (one the checker finds out of reach asks you how to go on); accepted, the
campaign is achieved and you are told (the notifications under the web UI and the phone's pushes), as you are when the
emergency stop, `campaign_max_sessions` or `campaign_failed_sessions` (item 724) stops it.
- **Only you stop it.** Campaigns, in the sidebar, lists them, and a row has buttons for what you can do to its campaign
  without opening it: Stop while it is active, Mark achieved until it is achieved, and Delete once it is not active and
  none of its sessions works; each asks first. A campaign's page shows its targets, the questions and the steps kept for
  you waiting (item 484), its ledger and its sessions, with Stop (which also stops a session working then), Go on, Mark
  achieved and Delete. The emergency stop (its button, or the pointer in the top-left corner during a desktop step)
  stops the campaign of the session it ends, and while the stop is engaged no session starts. `dotx campaign` changes
  the campaign without the daemon, so a session working then goes on until it ends (Stop on its run page ends it).
  `campaign_max_sessions` (0: no limit) stops a campaign after that many sessions; Go on gives it one more.
  `campaign_failed_sessions` (3; 0: never) stops one whose last sessions failed that many times in a row, with a note
  in its ledger that says so and you told (item 724): one was tried again every `campaign_gap_minutes` for ever, its
  page saying when its next session was. A claim of a session still working after your Stop leaves the campaign
  stopped: `campaign_achieved` achieves only an active campaign (item 724), while your own Mark achieved still does.
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

**The browser of a campaign.** A campaign works in the Chrome `[browser] mode` names, as every other run does, unless
you start it with `--browser own` or `--browser attach` (or `browser` in the API, which goes with `standing` only),
which pins that campaign to that Chrome. One that names none reads the mode again at each session, so a switch in
Settings › Browser reaches its next session, a campaign at work too (item 579). A campaign that asks for your Chrome is
refused at its start while that Chrome is closed or does not allow the connection (`dotx browser attach` sets it up),
so its sessions do not fail one after the other. The brief of every session says which Chrome it works in, and so do
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
