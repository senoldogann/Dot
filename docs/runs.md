# Runs and tools

## How a run works

A run is a loop. The model answers with tool calls; Dot checks each call against the [policy](safety.md#safety), runs the ones
that pass (up to `parallel_tools` at once) and hands the results back, until the model calls `finish`.

A chat keeps its turns on one page (item 374): each earlier turn reads as its question, what it did and its answer,
with no "open run" to hop through. What it did is what its run said, in the order it said it, and each stretch of its
tool calls as one line that says what they did ("Ran 2 commands and read 1 file"), which opens to every call and its
result, as on the run's own page: a command reads as the code it is, in typewriter type, and opened it shows the
command itself over its output (one that carries more than its command, a working folder for one, shows its arguments
whole); a read wears an eye and a write a sheet of paper, and each says what it did ("Read notes.txt") without its
tool's name, which a call keeps only where its words do not say what it is (item 506). A page
the turn's run showed with `show_page` stands where it came, in its frame
(item 426). That line opens and folds on a click while the run still works and after it ends,
and the reader's choice stays; folded, it still says what call runs now, except on a phone, where it keeps only the count.
Appearance › *Fold tool steps* says whether
those lines start folded (Neutral and the palettes fold them, Paper does not). The short marks at the left of the
conversation stand for its messages, and a click on one goes to it — the last of them to the newest question and the
work under it. A very long conversation keeps its last forty turns on the page and says how many it leaves out; the page
reads the latest 200 runs of a chat, the most the daemon lists at once, so a chat of more says that its earlier runs are
not shown, with no count (item 559). While a
chat is read, its page shows the shape of the conversation to come — a message of yours and the answer under it, under
Dot's mark and the line that say the chat is opening — so an empty page never stands in its place (item 502); with
reduced motion it stands still.

`finish` takes the `answer` and a `success` flag. The `answer` is the complete message the user reads (the findings, the
figures or the text that was asked for, with the links to the sources), and it is the result of the run: the web UI
shows it as the last message of the conversation, `dotx run` prints it. A `chat` turn has no `finish`: its plain reply
is the answer (a small model that is offered both writes the call out as text).

- Old tool results and screenshots are trimmed, and the oldest part of the conversation is summarised by the fast
  model (`[models] fast`, the side work the run's slower model need not do), so a run stays inside its context budget:
  the model's own context size (Settings › Models, item 250), else `context_tokens`: by default `"most"`, three
  quarters of the model's window (item 396), or a number for every model, cut to three quarters of the window when
  that is less. A wider budget means fewer summaries in a long run and more input tokens a step (up to twice as many at
  the end of a long run); 96 000 was the default from 2026-10-11 and 48 000 before, and an owner on a metered key who
  weighs tokens over summaries sets a number, or a smaller size for a model. The
  summary may use a tenth of the budget and never more than 1 500 words, so a long run's summary does not grow with
  each compaction until every step compacts, and does not cost the fast model minutes of writing. A compaction keeps as
  many of the newest messages as fit next to the system prompt, the tool definitions and its note in 85 % of the size at
  which it starts (`context_summarise_share` of the model's context size, or the whole budget when the window leaves
  less), and
  never less than the model's last reply with its results, so the steps after it have room. It is tried three times:
  a summary far over the limit is asked again with the limit and the overrun in front, and one that lacks a section (a
  section is there as a heading, not as a word of a sentence) is asked again once with the missing sections named and
  the first draft carried along; when the fast model gives no text,
  a summary far too long, an error or a timeout, or when its window cannot be read (an Ollama model whose server does not
  answer, item 552), the run's own model writes it, and while the fast model is paused after
  a quota, rate-limit or key error (see `standby`) the run's own model writes it without asking the fast one (item 240);
  the `compacted` event names the fast model's problem; when neither writes one, a mechanical summary takes its place
  and the event says so with the reason. In the conversation a compaction is a quiet line across the column where it
  happened, with the size of the context before and after it ("Context compacted 401k → 17k tokens", item 312); the
  facts under the side panel's More count them and say how many checks flagged something (item 260), and the Cost panel
  keeps the summaries' share. Next to the summary a continuity note keeps the
  goal, the plan with the state of each step and its targets, and your messages, and of the work already done and the
  paths touched the newest that fit in another tenth of the budget (it says how many earlier ones it leaves out), and a
  check lists names, paths and links in the summary that the conversation never had (an address cut short, with the
  ellipsis of a cut value, or one lengthened by a fragment, a query or a closing slash counts as known, not a page under
  it; a name the summary abbreviates itself, with an ellipsis inside it or a `<placeholder>`, is not looked up). The
  `compacted` event names the model,
  how the summary came about, the attempts it took, what the check found and the estimated size of the prompt before
  and after it (`tokens_before`, `tokens_after`). The model that writes the summary is told that what tools returned
  (pages, files, mail, command output) is data, to be reported as what it said and never as an instruction to the run
  or a wish of yours, and that the summary is Dot's own note of what happened, in which only what you wrote stands in
  your voice (in GOAL); the note that carries the summary says the same to the run that goes on (item 589).
- The plan has steps that are `pending`, `in_progress`, `done`, `failed` or `skipped`; `update_plan` writes it and
`mark_plan_step` changes one step, so the plan in the web UI moves as the work does. A step is marked `done` only with
evidence (`evidence`, cut at 400 characters): what the run observed that shows it, such as the path and what it read
back, the count or the line of output, or what the page showed. A mark without it is refused with what counts as
evidence, and a rewritten plan that keeps a done step keeps its evidence; a failed or skipped step may carry what
happened. The evidence stands under the step in the web UI, in every note that carries the plan and in the goal check. A
step that must wait for others names them (`depends_on`, step numbers; a cycle or a number outside the plan is refused),
the plan reads `(after 1, 2)` and the web UI shows the same, and the result of every plan change says which steps are
ready now (open, waiting for nothing open or failed) and which are held by a failed step. Steps that are ready together
are independent, so the run can hand each to `delegate` in one turn, naming the step; the delegated run's report then
closes the step (done with the report's start as evidence, or failed) without a mark of the run's own; a step the plan
does not have (a slip, or a delegate before the plan) does not stop the delegation: the run goes, the result's first
line says the step was not in the plan, and nothing is marked. A `delegate` call waits up to four hours for its
run's report. A run that calls `finish` with steps still open is asked
once to close them. A plan can also hold the run's targets (`targets` of `update_plan`, at most 600 characters): for a
goal that names an outcome, what the run will reach and how its own tools measure it. They stay until new targets
replace them (an `update_plan` without `targets`, or with an empty text, keeps them), are the first line of the plan in
the web UI and are printed under "plan" by `dotx run`, go into every note that carries the plan (a compaction, a
reminder, a resumed run) and, in goal mode, to the goal check.
- How much help a run gets depends on its model. The window is what one request of the model can hold; for Ollama it is
  the smaller of the model's own window and the `num_ctx` Dot sends with every request (the Models table of the System
  page shows it): `[providers.ollama] num_ctx` (65 536) for a model that runs on this Mac, where it sets the memory of
  the model's cache, and `num_ctx_cloud` (262 144) for a cloud model, whose tag is `cloud` or ends in `-cloud`
  (`kimi-k3:cloud`, `gpt-oss:120b-cloud`) and which runs on Ollama's servers; where the model's own window is larger,
  the table shows it beside the one Dot asks for ("of 262k (own 1.0M)", items 221 and 250). Such a model may be given a
  context size up to three quarters of its own window in Settings › Models, and Dot then asks Ollama for the window
  that size needs (the size is three quarters of it, in steps of 1024 tokens, at most the model's own) and the run
  plans with that same window (item 520): a 9B model of a 262 144-token window given 96K is asked for 131 072, whatever
  context length Ollama's own settings give it, since the window a request asks for wins. A cloud model whose own
  window is larger than `num_ctx_cloud` is given a larger one the same way; on this Mac a larger window takes more
  memory. OpenAI's and OpenCode
  Go's windows come from Dot's tables of their models, kept to their published pages; Anthropic's from its list of
  models. When three quarters of the window is less than the number `context_tokens` sets, or than 96 000 while it is
  `"most"`, the run's budget is that, and `tool_output_chars`, the most one tool result may carry, shrinks in the same
  proportion (never below 2000 characters). The text of a page or an application window in a browser or desktop result takes what is left next to the
  rest of the result, and never less than half the result, so a small window shortens a page instead of leaving it out.
  A model whose window cannot hold the system prompt and the tool definitions is refused at the start, with
  a hint. A model of the `weak` tier is reminded in task and research runs, every `reminder_every` steps, of the goal,
  your messages, the plan and its newest actions. `tiers` in `[agent]` sets a model's tier by hand; with
  `scaffold = "measured"` a model is weak when at least `profile_min_calls` tool calls of its newest 30 finished runs
  were measured and at least `weak_refusal_share` of them were refused before they ran (an unknown tool, or arguments
  that were not valid JSON or did not fit the tool). `scaffold = "fixed"`, the default, keeps every model standard.
  Every model, after three acting steps in a row whose `browser_act` or `desktop_act` calls carried one step each on
  the same page or application, is told once to batch the steps it can predict into one call (each call costs a model
  round trip; `dotx stats` counts the single-step share), and the timeline shows the note; `steer_single_actions =
  false` in `[agent]` turns it off. Every tool result records its channel (a command, an API, the web, the browser, the
  desktop, or the run's own tools), and `dotx stats` shows the calls per channel and the share of the acting calls that
  went through a GUI: the prompt sends the agent to a command or an API first, the browser second, the desktop last.
  The side panel's Run tab shows under Set up a budget cut to the window, why a model is weak, and each reminder
  (item 365).
- `tool_surface` in `[agent]` decides which tools a run's model sees. `full`, the default, offers all of them at every
  step. `grouped` starts with the core tools (files, the shell, the web, searching and saving notes, delegation, skills,
  the plan and `finish`) and the groups the thread already used. The other groups (`browser`, `desktop`, `schedule`,
  `terminal`, `memory_admin`, `drafts`, `accounts`, `team`, `pages`, and `mcp__<server>` for each MCP server) are
  listed in the system prompt with what they are for and
  their tools, at most 16 of a group: in a bigger group, those that share the most words with the run's goal first. The
  model turns a group on with `use_tools`. A call to a tool whose group is off does not run: it turns the group on, and
  its result asks the model to call the tool again. A group stays on for the rest of the run. `auto` is `grouped` for a
  model of the weak tier and for a model whose window cut the context budget, and `full` for every other model. A run
  of more than 128 tools starts `grouped` whatever the setting (item 524): some providers take no more in one request
  (Gemini documents 128 function declarations, and OpenAI's API has answered more with a 400), and an MCP server may
  offer any number of tools. The groups a grouped run has on (those its chat used, the servers picked for its message,
  those it turns on) may still bring a request past that; it then fails with the provider's error. The side panel's Run
  tab shows under Set up the groups a grouped run starts with, why it is grouped, and each group it turns on.
- `prompt` in `[agent]` decides how much the system prompt says (item 280). `full`, the default, says how to work and
  each kind of tool at length (the web, the browser, the desktop, memory, schedules). `lean` keeps the rules of trust
  and safety, of hesitating and of how a run ends as `full` has them, word for word where they are the same rule, and
  says how to work and each kind of tool in a line or two; the rest is in the tools' own descriptions. With
  `tool_surface = "grouped"` as well, a run starts lean: the shortest prompt and the core tools. Eval reports keep the
  style, and `dotx eval report` shows it beside the tool surface. `full` stays the default because the evals of item 280
  (in `ROADMAP.md`, with the numbers) did not show the lean start doing as well.
- `verify_answer_paths` in `[agent]` checks the paths a final answer names: `~` paths and absolute paths under
  `/Users`, `/tmp`, `/private`, `/var`, `/opt`, `/Applications` and `/Library`. When a task or research run ends with
  success and its answer names such paths that do not exist and that no successful call of the run named (a file the
  run removed or moved is not missing), the run is sent back once with the list: to create what is missing, to correct
  the answer, or to finish again with the same answer if it names them on purpose. The check makes no model call. It
  is off by default until evals show that it helps. The timeline shows each send-back.
- A run has no step limit. It ends when the model calls `finish`, when the progress watchdog stops it (see
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)), when a model call still fails after its retries and
  the wait for a model out of reach, at a limit of minutes or tokens you set, or when you stop it. In goal mode the goal
  check can also end it (see [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
- A goal that names an outcome instead of a task (grow, earn, reach, as many as possible, find a way) is held to the
  outcome in task and research runs: the model is told to record measurable targets in its plan before it acts, to
  measure after each round and change its approach when a round brought nothing, and to call `finish` only once the
  targets are met, when the next step needs something only you can give, or at the run's limit, then with `success`
  false, the numbers reached and what is missing. A precise request is still done exactly as asked. A chat is not told
  this: its turn ends with its reply.
- A reply without a tool call ends the turn in `chat` mode and when the run has not used a tool yet. Otherwise it is
  nudged towards `finish`; after `finish_nudges` nudges in a row the reply itself becomes the result. A step with tool
  calls starts the count again, so a status line written between tool steps does not end the run.
- A reply the model service cut short (at its output limit, or for another abnormal reason it gave) is never taken
  as the answer, in any mode: the model is told how the reply ended and asked to go on with its tools or give the
  complete answer again in fewer words. Each such reply counts as a nudge; after `finish_nudges` of them in a row the
  run fails with "The model's replies were cut short" and the reason the last one ended with. A reply in which the
  model declined (it ends with `refusal`: Anthropic's stop reason, and a refusal of an OpenAI model, on either of its
  wires) is one of them, so a run does not end done with a refusal for its answer (item 552); the review model's refusal
  is known for one too.
- A `finish` in the same reply as a call that did not work (an error, a refusal of the policy, a step kept for you; the
  plan's bookkeeping apart) does not end the run: its answer was written before the model saw the result. The run is
  sent back once with a note naming the call, and the timeline's data has a `finish_after_failure` event (item 552).
- A reply the service's content filter stopped (it ends with `content_filter`) is no nudge: asked again with the same
  conversation the model is stopped again (item 437). The run moves to a standby model at once (see
  [Helpers and standby](models.md#models-and-providers)), and the model is not paused for other runs; with no standby
  model left, the run fails saying that the filter stopped it and that another model would answer.
- `finish` can say what the run could not do without you (`needs_from_user`): the run's page shows it under the answer
  as "What Dot needs from you", and `dotx run` prints it after the answer.
- A small model with the full tool list sometimes writes the `finish` call out as a plain message (`gpt-oss:20b-cloud`
  did in most trial runs). Dot does not reinterpret such text: it is nudged first when tools were used, otherwise it
  becomes the result as written. Use a larger model as `main` for task and research runs.
- At most `max_concurrent_runs` runs go at once; the others wait. Delegated runs do not count.
- Runs at work at the same time know each other (item 301). Each chat has its own tabs, which no other chat's run
  reaches, and the browser keeps the tabs of a chat with a run at work however long they idle. Each run's model is told
  which other runs are at work, with their goals, the sites of their pages, the applications they drive and the files
  they changed, whenever that changes. A call that would type or click in an application another run acted in within
  the last 120 s, or change a file another run changed since this run last read it (or that it never read), does
  nothing and tells the model why, so that it works elsewhere or reads the file again first. Item 305 closes the other
  paths to the same app and file: a batch of desktop steps stops at the step that moves to an application another run
  holds (the steps before it stay done); the applications an AppleScript names count as driven, so a script into one is
  refused the same way; app names match whatever their case; the check and the footprint are one step taken right before
  the call runs, again after a wait for your approval; the files a shell or terminal command, or a shell line of an
  AppleScript (also one that drives a free application), writes or deletes (a redirect, `cp`, `mv`, `rm`, `tee`,
  `sed -i`, ... and a whole folder for `rm -r`) count as changed by it, as far as the line names them; the files a run
  changed stay its own for the other chats' runs at work after it ends, until they read them again; and the browser's
  mode and its debugging port do not change while another chat's run is at work. The prompt tells each run to leave the
  other runs' apps and files to them and to stop, kill or quit no process or app it did not start. Only the runs of one
  daemon know each other (`dotx run` in a terminal has its own), a path only the running line knows (`$(...)`,
  `find -delete`, git) leaves no footprint, and a process killed by name (`pkill`) is kept to only by the prompt.
- Runs, their events and their conversations are stored in a SQLite database (WAL mode; the daemon writes through one
  connection, one writer at a time, and waits out a lock held by another process with logged retries before it gives an
  error; a token count that cannot be written is logged, not fatal to the run), so the web UI can replay them. The end
  of a run that started is one write: its status, the scores of the notes it used and the event that tells of its end
  are stored together or not at all, and a delegated run whose end could not be stored is an error for the run that
  delegated it. A run that is stopped stops the runs it delegated, and its end is stored after theirs, so that its page
  shows each of them ended; the page of a run stored the other way, before item 197, reads how they ended from the
  database. An end the database refused (a full disk, say) is tried again every half minute until it is stored, with the
  event that tells of it and what follows it (the chat's next message, a campaign's next session); until then the run
  stays as the database has it, and a Stop says its end is on its way. A run that could not be started is stored as
  failed with that event too, so a page that showed it queued sees it end. A run that a crashed daemon left behind is
  marked as failed the next time Dot starts, as ended at its last event (the runs it delegated before it), and so is one
  that a `dotx` command left when it died, by the daemon within half a minute when it runs. Either can be resumed, and
  so can a run that a shutdown of the daemon cut off (see
  [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)).
- A message sent to a running run (steering) reaches the model at once: a step whose model is still answering is cut
  short and asked again with the message (item 509); a tool already at work finishes first, and a step past the run's
  limit ends with its finish. It also clears the run's untrusted mark (see [Safety](safety.md#safety)); a reaction
  on one of its messages reaches it the same way and clears nothing (see
  [Reactions on Dot's messages](#reactions-on-dots-messages)).
- A run belongs to the daemon, not to the page that started it: leave the chat and it goes on, its status stays live in
  the sidebar, and you can steer it when you come back.
- A run of a `dotx` command (`dotx run`, `dotx resume`, `dotx chat`) is on the pages too while the daemon runs: it works
  in the sidebar, and its page follows it live, as it follows the daemon's own runs, though its text
  comes whole at each step rather than as it is written. Its Stop asks the command to stop it, which it does within a
  second. A question or an approval of it can be answered on the pages, which ends the prompt at its terminal; the first
  answer stands, wherever it was given. It takes no steering from the pages: a message sent there waits in the chat's
  queue. A chat has one run at work at a time, whichever process drives it: a second is refused (409), and the run a
  `dotx` command left at work when it died is closed first.
- A message sent to a chat while its run works waits in the chat's queue: it starts as the chat's next
  run, with the mode, model, reasoning, approvals and goal mode it was sent with, once the run before it has ended done,
  one message after the other. A stop, a failed run or a crash of the daemon holds the queue until you resume it; a
  shutdown of the daemon does not, and once the run it cut off is resumed and the new run ends done, the queue goes on.
  The daemon keeps the queue, so it goes on with no page open, and a queued message can be sent to the run at work
  (steered) instead. A message starts once, and only after the run at work on the chat has ended, in whichever process
  that run worked: the daemon moves the queue of a chat whose run a `dotx` command drove once it sees that run end, and
  holds it when it starts after such an end. While a message's run is starting, editing it, taking it out or steering it
  is refused (409). A campaign's chat has no queue: a message there steers its session.
- `/btw <question>` in the box of a run (the slash menu offers it) asks on the side: the run's model answers from what
  the run has done so far,
  without tools, and the run neither sees the question nor stops for it. Several can be asked at once. The question and
  the answer are `aside_question` and `aside_answer` events, never messages of the conversation; the answer has the
  secrets Dot holds masked, as a run's own replies do (item 551).
- `/loop <rhythm> <message>` in the box of a run (item 281) or of a new chat (item 400; the slash menu offers it in
  both, `/loop 30m check the build`) makes the message a loop of the chat: the message goes at once (in a new chat, as
  the message that starts it), and a schedule of the chat (item 291) asks it again every 5, 10, 15, 20 or 30 minutes or
  every 1, 2, 3, 4, 6, 8 or 12 hours, in the chat, as its follow-ups start (its model, reasoning level, speed, approvals
  and goal mode, and as its agent in a chat with one of yours, item 463). Each run of it is told how the loop ends: it
  deletes its schedule (`schedule_delete`) once the work is done for good. Right above the composer, in the bar that
  also says how long the run has worked and how its helpers stand (one pane of glass on a phone, item 401), a line shows
  each loop of the chat, how often it asks again ("every 30 minutes", and "every day at 10:00" for a schedule a run of
  it made, where it read `cron 0 10 * * *` before item 590), when next, and a Stop that deletes it; the Stops stand in
  one column, on a phone the message stands over how often it asks again, and the rows fold under a head that says how
  many loops there are and, folded, when the next one asks, the fold kept in the browser (item 591). A rhythm a cron
  expression cannot keep exactly, or one shorter than `[scheduler] min_interval_s`, is refused (422); a new chat then
  starts nothing, nor when `[scheduler] max_schedules` are switched on already. A loop does not go with Keep going,
  whose campaign goes on by itself.
- Files can go with a goal or a steering message (attach, drop or paste them in the web UI; at most 10 of 20 MB
  each). In the box they stand in one row that wraps only when it runs out of width, a photo as its own picture at a
  thumbnail's size with its name as its tooltip, another file with its icon, its name and its size. A text pasted into
  the box that is long (2,000 characters or 40 lines) goes as a file of its own (item 269),
  `pasted-text-<time>.md` when it reads as Markdown and `pasted-text-<time>.txt` otherwise, which the model reads as it
  reads any attached file, as material rather than instructions; the pencil on its chip puts it back into the box as
  text. A message of files alone goes too (item 445): a send with an empty box and a file or a picture starts the run
  with the line its files are worth — "See the attached file." for one, "See the attached files." for more — and the
  files attached; with neither, the box stays put, and on a run at work a send with a file listed is a send, never the
  Stop an empty box makes it. Text, code, PDF, Word, Excel and PowerPoint files are put into the message as text (a long one
  shortened, with its path to read the rest), images go to the model as images or, for a model that cannot see, as the
  `vision` model's
  description. A Word document (`.docx`) reads as its paragraphs and tables in order, headings and list items marked as
  Markdown marks them; a workbook (`.xlsx`, `.xlsm`) sheet by sheet, each cell after its address and a formula with the
  value Excel last worked out for it (up to 100,000 cells, and 50 places of its rows for each of those, so that a sheet
  whose rows run far to the right with little in them is read in part, and the text says where it stops); a
  presentation (`.pptx`) slide by slide, with its tables, its charts' series and its notes; `read_file` reads such a
  file on disk the same way. A file of before 2007 (`.doc`,
  `.xls`, `.ppt`) or one locked with a password is refused, saying what to save instead, and so is a package that is
  damaged, or has a part packed in a way Office does not use, as a file that cannot be read. Anything else is refused at
  upload. The files are kept in `workspace/attachments/`, where the agent's tools can open them; one named like a file
  Dot keeps next to it (`meta.json`, `text.txt`) gets an underscore before its name.
- A file opens beside the conversation (item 195). A sent file's chip, a file in the box once uploaded (item 269) and
  a file's path an answer names in code (under `~/` or `/Users/<you>/`; Dot names the files it made by their full
  paths) open it in the side panel (the Run tab's list of the conversation's files went with item 544), which takes
  half of the page while it has its own width, and the whole screen on a phone. A PDF shows its pages as pictures the
  daemon draws with Core Graphics as they come into view; a page more than 20,000 px tall at the width asked is refused
  rather than drawn (item 386). A Word document, an Excel
  workbook and a presentation show first as macOS's Quick Look draws them (item 268), as Finder would: their fonts,
  fills, pictures and shapes, a workbook's sheets one tab each (Quick Look leaves hidden sheets out and shows the values
  the file saved). The daemon asks `qlmanage` for the drawing once per version of the file, keeps it in `looks/` in its
  home (emptied at its start) under an id nobody can guess, and serves it at `/looks/<id>/` in a frame that runs Quick
  Look's scripts sandboxed, with an origin of its own that reaches no server; **Contents**, a click away, shows what Dot
  reads of the file, and is what shows when Quick Look cannot draw it, with why. In Contents a workbook, and a CSV or
  TSV table (split at its commas, or at its semicolons where a comma marks decimals), a grid with a tab for each sheet,
  a click on a cell saying what it holds (its formula and the value last worked out); a Word document its headings,
  paragraphs, lists and tables; a presentation its slides with their text, tables, charts' series and notes; an image
  itself; a Markdown file (`.md`, `.markdown`) formatted as an answer is, with its headings, lists, tables and code, and
  as it is written a click away (item 269), its HTML never drawn; any other text as text, never as a page. On a page
  without the panel (the new chat's, an agent's) the file opens in a dialog. **Copy the text** in the panel's head takes
  what the panel reads of a text or Markdown file, a PDF (its text, page by page, up to 512 KB), a Word document or a
  presentation (item 403). **Open on the Mac** opens a document in its application (`open`), never a script or a
  program. Close or Escape gives the panel back as it was. The file stays as the chat goes on at its next run (a message
  sent, an answer that came; item 406), until it is closed; a narrow page, where it would cover the conversation, lets
  it go. The panel shows files of the workspace and of the home folder outside hidden folders and outside `~/Library`,
  but for the folders cloud drives sync (OneDrive's and the others' in `~/Library/CloudStorage`, iCloud Drive's in
  `~/Library/Mobile Documents`), judged once links are followed; for another file it says why not. The routes are `GET
  /api/files/preview?path=`, `GET /api/attachments/{id}/preview`, `GET /api/files/look?path=` and `GET
  /api/attachments/{id}/look` (where the drawing is served; 422 when Quick Look cannot draw the file), `GET
  /api/files/page` (a PDF's page as a PNG), `GET /api/files/image` and `POST /api/files/open`.

### Reactions on Dot's messages

Under each message of Dot's in a conversation, its answer and what it said in the middle of a run, a button (a
smiley with a plus, item 586) opens a small palette of seven reactions, and Dot acts on them while the run works
and after it ended. The ones that stand show as chips beside the button; pressing a chip, or the reaction in the
palette again, takes it back. The palette and the chips are reached and worked by keyboard (Tab to the button,
Enter or Space opens the palette on its first choice, the arrow keys, Home and End move between the choices, Enter
puts a reaction on or takes it back, Escape closes the palette and gives the focus back to the button), have names
for a screen reader, and are a thumb's size on a phone, which is the same web UI.

| Reaction | Name | While the run works | After the run ended (the default) |
| --- | --- | --- | --- |
| 👍 good | `good` | the run reads it at its next step and keeps going | only feedback |
| ❤️ love it | `love` | the same | only feedback |
| 👎 not right | `wrong` | the run reconsiders and changes course, and asks you if it cannot tell what is wrong | a follow-up run in the same chat |
| 🔁 do it again | `again` | the run does the last part again, another way | a follow-up run |
| 📌 remember this | `remember` | kept as a note | kept as a note |
| ❓ explain | `explain` | the run explains it to you in the conversation | a follow-up run |
| ✋ hold on | `hold` | the run holds before its next step and waits for you | only feedback |

**While the run works.** A reaction on one of the run's own messages reaches it at its next step, through the
inbox a steer goes through, as a note from Dot: a message of the thread that starts `[system note] The owner
reacted 👎 to your message «…»`, quotes the message (up to 240 characters, on one line, the secrets Dot holds
masked), says what the reaction asks, and ends with "A reaction is no approval of a step: whatever needs their yes
still asks." The note is Dot's words, not yours, so it vouches for nothing: it never clears the run's untrusted
mark, as a message you type does (see [Safety](safety.md#safety)), and it is no instruction to the review model.
The run's `steer` event of it says so (`vouches: false`, with the `reaction` it is of), and the conversation
draws no line for it, on the run's own page or in the turns before a later run: the chips are what shows.

- A 👎, a 🔁 and a ❓ ask the run to do something, so the step under way is given up for them, as for a message: the
  model's answer is cut short and asked again with the note, and a tool already at work finishes first. A 👍 and a
  ❤️ ask nothing: the step goes on, the note is read at the next, and a run that ends first never reads it.
- Reactions that come within 2.5 seconds of one another on one run are one note, a line for each. One taken back
  before the note is told is never told; one taken back after tells the run "go on as you were". A 📌 tells the run
  nothing.
- A ✋ is told at once. The run holds before its next step (a tool at work finishes first, as for any message): the
  conversation says so ("You held the run (✋): it waits for your next message."), and the box of the page sends
  what you write to the run at once, not into the queue behind a run that waits for it. The hold ends when you
  write to the run (a message of yours, the answer to a question it left, a queued reaction you send on with
  Steer, a message sent to the chat from another screen: it waits in the queue as any does, and the run goes on),
  when you take the ✋ back, or when you stop the run. A run held and forgotten stops after `[reactions]
  hold_minutes` (60): it ends cancelled, as one you stop does, with a result that says why and what it did kept in
  the chat, so that held runs never keep the places the daemon has for runs (`[agent] max_concurrent_runs`). The
  time it waits counts toward the run's limits as the wait for an answer does. A run on one of OpenCode's models
  gets the note and no hold.

**After the run ended.** A 👎, a 🔁 and a ❓ start a follow-up run in the same chat, as your own message would if you
typed it in the box: its goal is "My reaction 👎 to your answer above: that is not right. Reconsider it and change
what is wrong; ask me if you cannot tell what." (🔁: "do it again, another way."; ❓: "explain it to me."; for
something the run said in a step, "your message" stands for "your answer"). It starts as a run of the web UI with
what the chat's box chose (its mode, model, reasoning level, approvals, goal mode and agent, as a message typed
there), and the page goes on at the run it starts, as it does for a message typed in the box (item 451). When a run
works in the chat it waits in the chat's queue as a message does: edit it (files too), take it out, or Steer it,
which sends it on to the run at work. The files you put on it go with it, whether it starts as the chat's next run or
is sent on. Sent on, its words stand in the conversation as your message, on the run's page and in the turns before a
later run, while the run reads a note that vouches for nothing.

- The goal holds no quote of Dot's. A reviewer takes your words as your instruction, and what Dot said may repeat
  what a stranger wrote, so the message is quoted under your words, for the run alone (up to 600 characters), as
  Dot's own earlier text and no instruction.
- The run starts as untrusted as the run of the message, or the chat's last run, ended, as any follow-up does
  (item 508): a message you type starts a run unmarked, and a reaction is too small a word to vouch for what a run
  read.
- At most one follow-up run starts for a message and a reaction (taken back and put again, it starts none more),
  and at most `[reactions] follow_ups_per_hour` (20) in an hour in all; past that the reaction is kept as feedback
  and the page says so. What a delegated run said and what a campaign's session said are kept as feedback and
  start nothing: the campaign plans its own sessions.
- A 👍 and a ❤️ are kept as feedback, and so is anything a setting says is only feedback (below). The reaction is a
  row of the store (it stays until it is taken back, and goes with its chat), and an event of its run
  (`reaction_added`, `reaction_removed`: the chat, the message, the reaction, what it did and the run it started),
  which every page hears.
- A run of a `dotx` command that still works takes a reaction as feedback only: nothing can steer it from the
  pages. A reaction that reaches a run as it ends, before it reads the note, is done as after the run, and
  the pages hear what it came to (the run it started) as they hear any reaction.

**The pin.** A 📌 keeps the message as a note, which you chose: its words on one line, the marks of its Markdown
left out, cut at a word to the size of a note (`[memory] max_chars`, 600) when it is longer, tagged `from-chat`,
one of yours (it never rests, as a note you wrote yourself in Settings › Memory does not). It is marked as read
from outside content when the run it came from read some at any point, as a note a run saves while it reads such
content is: such a note is never handed to a run on its own, and a search that finds it marks the run that read it
(see [Safety](safety.md#safety)). A message that holds something that looks like an API key or a private key, or a
secret Dot holds, is not kept, as the memory tools refuse such a note: the 📌 is refused, says why and stays off.
Taking the 📌 back forgets the note when it is as it was made; a note that was there before, or that you changed
since, stays.

**Settings.** Settings › General › Reactions has the switch **Start a run for a reaction** (`[reactions]
follow_ups`; off, every reaction is only kept as feedback and a 📌 still makes its note) and, for each of the six
other reactions, what it does after the run it is on has ended: **Start a follow-up run** or **Only feedback**
(`good`, `love`, `wrong`, `again`, `explain` and `hold` of `[reactions]`). A change holds at once and is kept in
`config.toml`. `follow_ups_per_hour` and `hold_minutes` are set in the file. The routes are in [the daemon
page](daemon.md#the-daemon-the-web-ui-and-the-service).

## Tools

| Area            | Tools                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------ |
| Shell and files | `shell`, `read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `search_files`, `write_notes`           |
| Terminal        | `terminal_open`, `terminal_send`, `terminal_read`, `terminal_close`                                                |
| Web             | `web_search`, `web_fetch`, `web_crawl`, `web_extract`, `judge_items`                                               |
| Browser         | `browser_open`, `browser_snapshot`, `browser_find`, `browser_links`, `browser_read_text`, `browser_wait_text`, `browser_act`, `browser_eval`, `browser_scroll`, `browser_screenshot`, `browser_upload`, `browser_back`, `browser_new_tab`, `browser_switch_tab`, `browser_close_tab`, and while 1Password is connected `browser_logins` and `browser_fill_login` (item 493) |
| Desktop         | `desktop_apps`, `desktop_open_app`, `desktop_snapshot`, `desktop_read`, `desktop_act`, `desktop_screenshot`, `desktop_clipboard_read`, `desktop_clipboard_write` |
| macOS           | `apple_script`, `notify`, `code_fetch`                                                                             |
| Memory, time    | `memory_save`, `memory_search`, `memory_update`, `memory_forget`, `schedule_create`, `schedule_list`, `schedule_delete` |
| Orchestration   | `delegate`, `consult`, `update_plan`, `mark_plan_step`, `ask_user`, `finish`, `use_tools`                          |
| Team            | `create_agent`, `message_agent`                                                                                    |
| Campaigns       | `campaign_targets`, `campaign_note`, `campaign_schedule`, `campaign_achieved`; with TypeSafe's key `rank_posts`, `pick_draft`, `check_draft` |
| Pages           | `show_page`                                                                                                        |
| Drafts          | `draft_message`                                                                                                    |
| Mail, calendars | `mail_search`, `mail_read`, `calendar_events`                                                                      |
| Skills          | `skill`, `find_help`                                                                                               |
| MCP             | `mcp__<server>__<tool>`; the server `web-research` comes with Dot (item 583): `mcp__web-research__web_search`, `web_scrape`, `web_map`, `web_extract` and the crawl tools `web_crawl_start`, `web_crawl_status`, `web_crawl_results`, `web_crawl_cancel` |

The shell and the file tools work in `<home>/workspace` unless a path says otherwise. Dot is told to change the text
of a file with `edit_file` and to write a file whole with `write_file`, and to keep the shell for running things
(tests, builds, git, formatters), one mechanical change across many files and a file that is not UTF-8 (item 382): what
the file tools change goes into the undo journal, shows in Changes and can be undone (within the journal's limits on
a file's size and a run's copies, under [Autonomy](autonomy.md)), and what a command writes does not. Nothing stops a
command from writing a file. Any error of a tool fails that
call, not the run: the model reads what went wrong (for an error no tool expects, its kind and its words, while the log
keeps its traceback) and goes on. `edit_file` changes the text it is given and nothing else: a file that breaks its
lines with CRLF keeps them, in the new text too, a file that is not UTF-8 text is refused, saying so, for the shell
to change, and an empty text to replace is refused (item 556). A file the file tools make gets the permission bits
any program's new file gets (`0666` less your umask), and a file that was there keeps its own. `read_file` reads a
long file in pages of lines, the text of a PDF too, each page ending with the line to go on from, so no part of it is
out of reach; an image goes to the model as an image, an iPhone's HEIC photo too, which macOS draws first (item
556). A shell command runs with the `PATH` of your login shell, in its own process
group (cancelling a run ends the whole group) and under a timeout (`[shell] default_timeout_s`, at most
`max_timeout_s`). An AppleScript (`apple_script`) ends with its call as well: one whose run is stopped or whose call
times out is killed. `web_search` climbs a ladder of engines until one answers (`[web] search_backend`, default `auto`;
`dotx doctor` shows the ladder): the owner's SearXNG instance when `[web] searxng_url` is set (its own engines compose
the answer, no key; a loopback instance is the usual case and is trusted configuration), then the hosted search of
Ollama when `OLLAMA_API_KEY` is set (no bot check), then the Anthropic API's own web search when `ANTHROPIC_API_KEY` is
set (relayed by `[models] search`, Haiku 5.5 by default; the API bills $10 per 1,000 searches next to the tokens, a
failed search is not billed; `[web] anthropic_search = "first"` puts it before Ollama, `"off"` leaves it out), then the
HTML pages of DuckDuckGo and Brave Search (no key), then DuckDuckGo opened in Dot's own Chrome when the run's browser is
Dot's own with a visible window (its bot check runs there and passes; a headless Chrome is shown a CAPTCHA, measured
2026-10-11). An engine that limits this machine is benched (DuckDuckGo 10 minutes, Brave 90 seconds, Ollama 1 minute,
SearXNG and the Anthropic rung 2 minutes) and the searches of that time skip it at once. An Anthropic answer in which
the relay did not search is no result: the next rung is asked (a search that found nothing has its result block). The
same query within 10 minutes is answered from memory, when it was asked of the same rungs for at least as many
results; a larger `max_results` or another `search_backend` climbs again (item 554). The first line of a result names
the engine that answered, every rung skipped with its reason (for an
engine that answered with an error: the HTTP status and the start of what it said, secrets masked, so an engine that is
down reads differently from one out of credit; the log's `search_engine_failed` and `search_engine_benched` carry the
whole context), and the age of a remembered answer. Bing is not a rung: without a session its page answers some queries
with the results of another query. `web_fetch` reads HTML and PDF and refuses localhost, LAN and cloud-metadata addresses unless `[web]
allow_private_hosts` is set. A body is read as it comes and inflated a piece at a time (gzip or deflate, once): a body
in more codings, or in one Dot does not read, is refused, so that a few hundred bytes of nested gzip are not gigabytes
in memory (item 554). A page that turns the plain request away (HTTP 401, 403, 429, 503 or 999, LinkedIn's
refusal) or shows little text without JavaScript is read in the run's Chrome, in a tab the owner does not work in that
closes after the read: a scratch tab of Dot's own Chrome, or a tab opened behind the owner's in the owner's attached
Chrome (item 98); the first line of the result says so. That Chrome is held to the same rule (item 554): a page it was
served from a private or local address, in any hop of its redirects or where it landed, is not given back (the request
for it may have been sent: a redirect of the page can have made Chrome send one, which a plain request would not),
and the crawl's reading in the run's own tab holds the page it landed on to it likewise. A host that refused a plain
request once is remembered for the run, and the next fetch of that host goes to the browser first. The plain request itself sends the headers a browser's
address bar sends (`Sec-Fetch-*`, `Upgrade-Insecure-Requests`) with the same user agent. A page that refuses the browser
too says that, with both answers, and names the next rung in one line: another source, `web_search` with the address as
the query for the hosted search's snippet, or `browser_open` and Take over for a login wall. A page that is not there
(HTTP 404 or 410) is reported with the links of the host's front page that share a word with the address asked for (a
dozen at most), so the next turn opens the right page instead of guessing another address; the system prompt says the
same (find a page with `web_search` or `web_crawl`, never by trying `/careers/` on a hunch). `web_search` returns up to
10 results; a larger `max_results` is lowered to 10 and the result says so.

**Crawling and extraction.** `web_crawl(url, scope, max_pages, max_depth)` reads many pages of one site in one call:
breadth first from the start address, following the links within the scope (`prefix`, the pages under the start
address's folder, or `host`, every page of its host), several fetches at once and the requests to one host spaced apart,
within the limits of `[web.crawl]` (`max_pages` 50, `max_depth` 3, `concurrency` 4, `per_host_spacing_s` 0.5; a call may
ask for less, never for more). Every page comes back as one clean text document (title, address, description, then the
text as `web_fetch` renders it, at most `page_max_chars`), each in its own `<web_content>` fence, under an index that
lists the pages with their depth, size and how they were read, the addresses left out and why, and how many more
addresses were found within the scope but not read. What the pages and their links wrote of the index (their
addresses, their titles clipped to 200 characters, the addresses left out, the ones `robots.txt` disallows) stands in a
fence of its own, and the lines outside it are Dot's; the links listed for a page that is not there, and the JSON of
`web_extract`, are fenced the same way (item 554). A page that shows little text without JavaScript (fewer than 400
characters and three or more script tags) is rendered once in Dot's own browser tab and read from the screen
(`render = "auto"`; `"never"` fetches only, `"always"` renders every page); PDFs are read as `web_fetch` reads them. A
page that cannot be read (a character set nobody knows, a PDF that is locked or will not open) is left out with its
reason, and the pages read stay (item 554); a level the page limit cuts goes on at its own depth, so no page is read
past `max_depth` or listed one level too shallow. A page that turns the plain request away is read the way `web_fetch`
reads one, in a tab the owner does not work in, and
the index says so (`rendered; the plain request got HTTP 403, read in the browser`); its links are not followed. A page
that refuses the browser too is left out with both answers. `robots.txt` is read once per host: with `robots = "report"`
the addresses it disallows are named in the index but fetched, with `"obey"` they are left out. The crawl goes through
the same client as `web_fetch`, so the address rules and the browser's host lists hold for every page. When the pages do
not fit one result, each becomes a Markdown file under `<workspace>/crawl/<host>-<time>/` with an `index.md`, and the
result says so: the run reads the ones it needs with `read_file` or hands them to `web_extract`, and reading them back
counts as untrusted content as the crawl did (item 494).

**Notes of a run.** `write_notes(content, append)` keeps the notes of a run in one Markdown file,
`<home>/workspace/notes/<run id>.md`: a long research run writes what it found there as it goes, with sources, instead
of holding it all in its context, and names the file in its answer. The file is in the undo journal like any file write.
It is the only file a `research` run may write, and the write lands on that file or nowhere: a link at the notes folder
or at the file, which a command of a `task` run can make, is refused, never followed (item 556).

**Pages in the conversation.** `show_page(title, html | path)` shows you a page the run writes in HTML, in the chat: a
chart, a table, a diagram, a 3D view, a mockup. It sits under its title in a frame as wide as the conversation and as
tall as the page (80 to 2,000 pixels), and Open shows it full size in your browser. It is drawn in the colours of the
theme on screen, given to it as CSS variables (`--background`, `--foreground`, `--accent`, `--chart-1` to `--chart-6`
and others), and takes the new ones when the theme or the system's scheme changes. Its scripts run sandboxed, framed or
full size: its origin is opaque, so they read nothing of the web UI and reach neither the API nor the page around them.
They fetch nothing but libraries from cdn.jsdelivr.net, unpkg.com, cdnjs.cloudflare.com and esm.sh, submit no form and
ask for no camera, microphone or position. A link you click in the page opens in your browser; a script of the page
cannot open one by itself, nor with a click you made elsewhere. A page that loads another page in its frame is closed,
with Show it again. A page of up to 500,000 characters is kept in the database under an id that cannot be guessed, which
is all it takes to read it (`GET /pages/<id>`, no token, never cached); it goes with its chat, and with the events of
its run under `dotx prune`. `dotx run` prints the page's address. The tool is in a group of its own, `pages`. The
system prompt asks for a page whenever an answer holds something with a shape (figures to compare, a trend, a
ranking, a share, a timeline, the steps of a process, how parts connect, a place or a layout), rather than a long
list or figures in text, one page a question and none for a single number. It asks for a page that lives: animated in
(bars that grow, lines that draw themselves, rows that slide in, a 3D view that turns), with values on hover, tables
that sort and series that switch on and off, and still under `prefers-reduced-motion`. It names the libraries that
fit, in pinned versions: Apache ECharts or Chart.js for charts, Mermaid for diagrams, D3 for a drawing of the run's
own, three.js for 3D (item 521).
A page the run made in its workspace (an `.html`, `.htm`, `.xhtml` or `.svg` file) is shown by its path instead of its
HTML (item 598): the file must lie in the workspace once its links are followed and not among the pages a crawl or an
MCP answer kept there, and the call is judged as a read of it, as `read_file`'s is. The files beside it that it names
are written into it, since a shown page loads nothing of this Mac (item 600): a style sheet becomes a style block, a
script its text, and a picture, a sound, a video or a font of the page, of its styles or of a tag's `style` a `data:`
address, each when it is one `read_file` reads without a question (in the workspace, not protected, not Dot's own, not
a crawl's or an MCP answer's) and the page with it stays one page (500,000 characters): a file that would not fit is
measured before it is read and left out, and the page is shown without it; a page that still comes out longer (a file
that grew while it was read) is shown as it was written. A sheet a style imports (`@import`) is not written in. The
answer names the files left out. What a script loads by itself (a module's imports, a fetch) does not load. The tool's
answer gives the page's address on this Mac (`/pages/<id>`), which the run's browser may open to see what it showed
(item 601, [Browser](browser-and-computer-use.md)).

`web_extract(sources, fields)` pulls named fields out of pages or files as JSON without the pages entering the main
model's context: for every source (an address, or a file: a path, or the name of a file in the workspace such as the
Markdown files a crawl wrote, read as a file even when the name looks like an address, `notes.md`) the fast model
(`[models] fast`) is asked once for every field's value and the exact words of the source it comes from, and each quote
is checked against the source, so the result says per field whether the value is `verified`; a value whose quote the
source does not hold is kept and marked `verified: false`, a claim to treat as one, and so is a value the fast model
gave without a quote. A file's text goes to the fast model, so a file in a protected location is refused, as `read_file`
refuses it. A source that cannot be read or extracted is listed under "Not extracted" with the reason; while the fast
model is paused after a quota, rate-limit or key error, no source is read or sent to it, and each is listed with the
pause (item 240). At most 20 sources and 20 fields per call. A call has 10 minutes, and ends at its own time rather
than at the tool timeout, which would lose every extraction already paid for (item 554): the source it is in when the
time runs out and the ones it did not reach are listed under "Not extracted", and what was extracted comes back.

A terminal session is for a program that does not run to its end by itself: one that asks questions (an installer,
`npm create`, `ssh`), a REPL (`python3 -i`, `psql`), or a server that must stay up while the run goes on.
`terminal_open` starts it on a pseudo-terminal of its own, with the environment of the shell tool, and returns its
first output; `terminal_send` types into it and returns the answer, `terminal_read` waits for more (a server's log),
`terminal_close` ends it and everything it started. The output is read as plain text, without a terminal emulator, so
full-screen programs such as vim or htop show as garbled text. The command and every line typed into it are judged as
a `shell` command is, a line typed in parts as a whole. Typing never holds Dot: what the program has not read yet waits
for it, up to 64 KB, and past that `terminal_send` fails with an error that says so until the program reads. A key a line editor acts upon (Tab, an arrow, a Ctrl- shortcut
other than Ctrl-C, Ctrl-U) leaves a line Dot cannot tell, and running it asks; once a line has run, the program may be
in another directory, so later lines are judged from the home folder. A chat keeps at most `[shell] terminal_sessions`
sessions open (3; 0 removes the tools), each closed after `terminal_idle_s` without use (30 minutes) and all of them
when the daemon stops; the end of a run closes none, so a server it started stays up for the next message.

A command that runs long and ends by itself, as an eval or a long build, past the 30 minutes that `shell` may wait
(`[shell] max_timeout_s`), or for minutes while the run has other work to do (item 508), goes to a terminal session in
the background (`terminal_open` with `background`, item 229): it is not closed while it runs for being left unread, and
the run that started it can end. When it ends by itself, its chat is told once (item 437), and never in a message of its
queue: the Queued list of the composer does not show it, and a stopped chat is not left paused with it. A run at work on
the chat is told at its next step, in a system note that says which command ended, with its exit status and how long it
ran, and nothing it printed, which the run reads with `terminal_read`, as untrusted; it is Dot's note, so it clears no
untrusted mark and is no instruction of the owner. When no run works on the chat, a run is started to tell it, with the
chat's choices, as the end of a run starts its queue's next message; its goal says the same words, Dot wrote them, so it
opens as a note and starts as untrusted as the chat's last run ended. That run starts only when nothing waits in the
chat's queue and the queue is not held, the chat's last run ended done and the emergency stop is not engaged (item
553: a command that ends after you stopped the chat, or while the stop is engaged, starts nothing); otherwise the run
that starts next, a queued message's, or yours after a stop or a failed run, is told at its first step, so your queued
messages and their order stay as they were. Right above the
composer, the chat's page shows each of its commands at work, with the time it has run and a Stop; the dot of a command
in the background pulses, as its end will wake Dot. A command stopped there, closed by the run or ended by the daemon's
stop tells nothing, and neither does one whose end a run has read itself with `terminal_read` before it was told (item
427): a run that waited for its command and saw it end is not told the same again. A delegated run or a campaign's
session opens none, having no chat of its own to tell, and neither does a `dotx` command, which ends with its run: a
command in the background lives as long as the daemon.

### Drafts

`draft_message` writes an email (Mail) or an iMessage (Messages) for you to send (item 314, after Grok's drafts): a
card in the conversation, where it was written, with the recipients, the subject and the words. **Send email** or
**Send message** hands it over as it stands; **Edit** turns the card into a form first, and **Discard** drops it, after
asking, with nothing sent. Nothing goes out before Send, whatever the run's approvals, a `yolo` run's too: a draft is
how a run has you look first, and the run goes on at once without waiting for your answer. Writing a draft changes
nothing outside Dot, so the policy allows it (a `notes_write` action, `safe`); a key in it, in an address too, is
refused as it is anywhere else. The tool is in the `drafts` group, and its tool result tells the model not to send the same message
another way. While two or more drafts wait, each card has a tick, and a bar after the last of them sends the ticked
ones together, one after the other, each with its own words. A draft can be answered long after its run ended, and on
another screen: an open page shows the answer as it comes (the lifecycle stream carries `draft_decided`), and the
phone hears of every new draft ("Dot drafted an email", with the recipients and the subject), as the web UI's
notification says it. A message draft to a number or address you paired with the iMessage channel is refused, when the
run writes it and when you press Send on a card whose recipient you changed to one: that conversation is yours to
write in, and Dot reads what is written there as your command
([Channels](channels.md#who-may-command-dot-through-this-channel)).

Send gives the draft to `osascript` with a fixed script for Mail or Messages; the recipients and the words reach the
script as its arguments, never as words of the script (`drafts/scripts.py`), and after `--`, so that a subject or a
message that starts with a dash is not read as an option of `osascript` (`-e` would add a line of script). A send the
application refuses leaves the draft "Not sent" with what the application said, and **Send again** retries it. Mail's
`send` answers whether it sent, and an answer of false (no account to send from) is a refusal too: the script ends in
the error "Mail did not send the message" (item 558). A message to several people is handed over one recipient at a
time, and one that fails partway leaves the draft "Not sent" with the names of those it had reached, which you take off
the recipients before you send it again, or they get it twice. A second press, here or on another screen, finds the
draft taken (409). A draft the daemon was handing over when it stopped is marked "Not sent" at its next
start, as whether it went out is not known: look in Mail's Sent mailbox or the conversation in Messages before you send
it again. Drafts are kept in the `drafts` table and go with their chat, or with the events of their run when those age
out. The API: `GET /api/drafts/{id}`, `POST /api/drafts/{id}/send` (`{"to", "cc", "subject", "body"}`: the words that
go; a message has no cc and no subject) and `POST /api/drafts/{id}/discard`. The scripts were not run against Mail or
Messages themselves, which would send for real: the tests compile them, and give them to a stand-in of `osascript` that
writes down what it was handed.

### Mail and calendars

`mail_search`, `mail_read` and `calendar_events` (item 424) read your mail and calendars in the Google and Microsoft
accounts you signed in on Customize › Connectors ([Accounts](extending.md#accounts-googles-and-microsofts-own-sign-in)),
through Gmail's, Google Calendar's and Microsoft Graph's own APIs, so that neither Mail, Outlook nor a browser has to be
open. They are a run's only once an account is signed in: until then neither they nor their part of the prompt are in
it. `account` is the address of one of the accounts: the run's prompt names them, and an address no account has is
refused with their list. `mail_search` takes Gmail's search words (`from:ada is:unread newer_than:7d`) or Outlook's
(`from:ada`, `subject:invoice`) and, without any, lists the newest messages of the inbox; `mail_read` reads one whole:
its people, its date, its text (the HTML of a message with no text made text) and the names of its files;
`calendar_events` lists the events between two days or moments, in the Mac's time zone, each repeat on its own, with
your answer to each. A list the provider cut says so (more messages match, or the time may hold more events), so that a
run narrows its search rather than taking the first page for all there is. Graph searches no events: with words to look
for, an Outlook calendar is read a page of 100 events at a time, up to ten pages, and its next pages are followed only
on Graph's own address, as they carry the account's token. They only read: nothing is sent, moved or marked read, so
research runs have them too, and the policy sees a read through the network (`network`, `low`). An email for you to send
is still a draft (`draft_message`).

What they return is written by others, since anyone can send you a mail or an invitation: it comes inside an
`<account_content …>` fence and marks the run untrusted, as a web page does ([Safety](safety.md)). A token the
provider turns away before its time is refreshed once and the call made again; a call the provider answers with 429 or
a 5xx, or that does not reach it, is made again after the wait it asks for (half a minute at most), three times in
all; any other refusal is the call's error, with the provider's own words. The tools are in the `accounts` group.
Their tests run against a stand-in of both providers after a real sign-in into a keychain of their own
(`tests/smoke/test_mail_calendar.py`).

## Delegation

`delegate(goal, model?, step?)` hands a sub-goal to a separate run with a conversation of its own and returns its
result, so a long investigation does not fill the parent's context. The run works on the parent's model, or on the
helper model that `model` names when that one can answer (see [Helpers and standby](models.md#models-and-providers)).
Several calls in one turn run side by side (up to `parallel_tools`), which is how a plan's independent steps run at the
same time; `step` names the plan step the part is, and the report closes it (see the plan above). A delegated run cannot
delegate again (the depth is one), ask you a question (its approvals still come to you) or consult a helper, does not
take one of the `max_concurrent_runs` slots, starts as untrusted as its parent, and ends when the parent is cancelled.
The parent's result names the model the delegated run ended on. The model cannot choose the delegated run's mode: a
research run's helper does research and only reads, and any other run's helper, a chat's too, is a task run. The helper
of a run of one of your agents carries the agent's id (item 553): its tools are those the agent is bound to, as its
parent's are, and its prompt holds the agent's instructions. A step it
hands to you, or a question for your yes, that nobody answers in time is kept where its parent keeps them, with
`[policy] unanswered = "park"` (item 485, [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)):
your answer goes to the parent, or to a run that goes on from it.
