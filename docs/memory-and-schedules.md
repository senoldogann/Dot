# Memory and schedules

## Memory

Dot keeps short notes between runs: facts about you, your machine and how you like things done. `memory_save`,
`memory_search` and `memory_forget` are tools of the agent; you manage the same notes with `dotx memory` and in the web
UI, where Settings › Memory (items 373 and 566) is the box that adds a note at its head (its tags beside it, one fact
per note, **Remember** or ⌘Enter; on a phone the tags have a line of their own and **Remember** stands at the right of
the line under them, and a touch screen is not offered the key, item 576), then **Notes** with how many there are, a
choice of All, Lessons, Preferences and From outside content (each reads the 2,000 newest notes, not only the ones
shown; with a search, the search's 50 best hits; one line in every window, which scrolls sideways on a phone when it
does not fit, item 576, and beside which the search stands, or under it when both do not fit, item 578) and their
search, the notes changed last first, 30 at a time with **Show more notes**, each a row with its tags
(labels the height of their words, on a phone too: item 576), the mark of a note saved while a run read outside content,
who saved it and when, how often it was used, and its edit and delete; then what Dot knows about you with your own
lines, then how learning fares; a note's actions show when the pointer is on its row (always on a touch screen);
forgetting, correcting or adding a note keeps the notes read and the list where it was, and a correction open on another
note stays open (item 204). In a run's conversation, a note the run saves, corrects (`memory_update`) or forgets leaves
a quiet line where it happened, what changed and the first words of the note: "Saved to memory", "Updated memory" or
"Removed from memory" (item 313, after Grok's "Updated memory"). The line of a saved or corrected note opens Settings ›
Memory with that note first, to read, correct or forget (`#/settings/memory?note=ID`, `GET /api/memories/{id}`); a note
forgotten since is said to be gone. A note saved again, or a call that failed, changed nothing and leaves no line. The
line names no agent: a note a run saves is Dot's, for every run (item 94). The notes a run is handed at its start leave
a line too, "Recalled 3 memories", not in the conversation but under Set up in the side panel's Run tab (item 365), and
only when its chat had not been handed one of them before: a follow-up handed the same notes says nothing, and one
handed a new note says how many are new, "(1 new)" (item 335). The notes themselves go under the message that recalled
them, in the note that gives the time ("[system note] It is now…", item 404), not into the instructions: the
instructions stay the same bytes from one message of a chat to the next, so the provider serves them and the history
from its prompt cache (the first word of an answer came after 30 to 50 seconds of a 200,000-token chat before;
`first_token_seconds` on each `model_call` event measures it from now on); a note an earlier message of the chat was
handed is not repeated while its text stands, comes again when it changed (a correction on the Memory page, a run's
update), and is named as forgotten when it was, since the old text stays in the chat; a note folded into a summary, in
the run or since, is handed again after the summary, as it stands then.

The notes can be put in order, pinned, merged and forgotten with your yes (item 584). **Sort by**, over the notes'
card and outside the choices and the search, lists them by when they were changed, when a run was last handed them,
how often, or the least used first (the notes no run was handed come first); a search lists by how well a note
matches, and the choice is off while it runs. The pin on a note's row keeps it awake: a pinned note never rests, and
pinning one that rests wakes it. A note that rests says since when ("Resting since 13 Oct"). A note the nightly
reflection merged into another says "Merged into another note", and the note that merged them says "Merged from 2
notes" and has **Undo this merge**: the notes it took in wake apart and the merged note goes to the trash.
**Proposed to forget** is a card above the notes, there while a note has rested for `[memory] forget_after_days`
(90): each note with **Keep** (it wakes, and is proposed again only after as long a rest) and **Forget**, and
**Forget all N**, which asks first and forgets the notes the card lists, all in sight in a box that scrolls. Nothing
is forgotten before you press one of them. A note you forget (there, in the list, with `dotx memory forget`, or by
the agent's `memory_forget`) is not deleted: it waits in the trash for `[memory] trash_days` (30), listed as
**Forgotten notes** under the notes with when each is deleted for good, **Restore** (it comes back awake) and
**Delete for good**; the nightly reflection deletes the notes that stayed longer, and no others.

How learning fares, the last card, says for the reflection, the lessons and the skills when each last went well and,
for one that failed, how many times in a row and why. A work with neither a success nor a failure recorded (one that
had nothing to learn from leaves no mark) says "nothing recorded yet", not that it has not gone well. The nightly
self-improvement (the harness's step) says "off" while `[harness] step_time` is `off`, and when it last went well if it
ever ran (item 613). Learning that
has stopped (a work that failed three times in a row, or no reflection that went well for two days while the
reflection is on) is told once, as "Dot's learning has stopped", and again only after it worked again; the clock is
not read while a reflection is under way, so the one that catches up after a Mac slept through its time tells nothing
before it ends (item 557).

```bash
dotx memory add --tag core "I prefer answers in Turkish, with code and commands in English"
dotx memory search --limit 5 how do I like my answers
dotx memory list --order used   # changed (the default), used, most_used or least_used
dotx memory forget NOTE_ID      # to the trash for [memory] trash_days, not deleted
dotx memory proposals           # the notes that rested for [memory] forget_after_days and wait for your yes
dotx memory forget --proposed   # forget them all (they go to the trash); dotx memory keep NOTE_ID... wakes some
dotx memory trash               # the notes you forgot, with when each is deleted for good
dotx memory restore NOTE_ID     # bring one back from the trash, awake (dotx memory purge NOTE_ID deletes it for good)
dotx memory pin NOTE_ID         # a pinned note never rests (dotx memory unpin NOTE_ID takes it off)
dotx memory unmerge NOTE_ID     # take back a merge: its sources wake apart, the merged note goes to the trash
dotx memory reindex      # embed every note again, for example after changing [memory] embed_model
```

- A run starts with the standing notes (newest first): tagged `core` and yours, a note you wrote or one you pinned. A
  run may tag a note `core` to ask that it stand; it stands once you pin it on the Memory page, which marks such a note
  **Core once pinned** until you do, and until then it is handed when it fits the goal, rests and may be proposed to be
  forgotten as any of a run's (item 613: notes runs had marked core, of a thesis and a job search, went to every run of
  a week). Then the notes you wrote yourself (the ones
  closest to the goal first), then the agent's notes close enough to the goal and the earlier goals of the conversation
  (`inject_min_similarity`; by fit and score), as many as fit a budget of tokens: `inject_share` (2%) of the model's
  context window, at most `inject_tokens` (1500; the cap alone when the provider does not say the window). Each of
  these groups first gets its own share, a quarter of the budget, and what is left then goes to them in the same order,
  so many notes of yours do not crowd out the agent's lessons. A note that does not fit is passed over, a shorter one
  after it may still fit, and `memory_search` finds the rest. A lesson is also tagged with the sites and applications
  of its run that it names (`site-amazon-com`, `site-notes`; item 308), and when a run first reaches a site or drives
  an application, its next step is handed the lessons tagged with it that it was not handed yet (at most five) and the
  learned skills written there that its instructions did not list (at most three), counted as handed like the notes
  it started with; the side panel's Run tab says under Set up "Recalled 2 memories learned on www.amazon.com", as
  it says there how many notes the run recalled and the tokens they took (item 365).
- Use decides what is recalled: a note handed to a run counts as used, and a search lifts the agent's notes a run was
  handed lately (a use a moment ago is worth a second place in one ranking; the lift halves every `use_half_life_days`,
  30), so the notes that help overtake the ones first written. An agent's note that no run was handed for `rest_days`
  (90), counted from when it was last handed or written, rests: the Memory page lists it under "Resting", a search
  still finds it, but runs no longer start with it on its own. Your notes and the ones you pinned (the standing ones
  among them) never rest that way.
- A note can hold for a while (item 306): `memory_save` takes `valid_days` for a fact that is true only so long (a
  trip, a deadline, an offer). The model is told until when it holds ("holds until 2026-10-20"); once that day passes
  the note rests, whoever wrote it, and neither a run nor a search is handed it; the Memory page says "Holds until …",
  then "Ran out …". Saving the same note again wakes it with the new time. Under each note the page says how the runs it was handed to
  ended ("Handed to 5 runs, 4 done"); every use is recorded with the run's end, which `dotx stats` reads.
- What Dot knows about you opens the memory part of every prompt: a short section the nightly reflection keeps (your
  language, hours, the tools and sites you use by their names, the ways you ask, the limits you set), rewritten each
  night from its previous version, your notes and the day's runs. The Memory page shows it first; the lines you write
  under it are yours, kept as they are, marked "(you said)" in the prompt, never rewritten, and the reflection never
  contradicts them (`GET`/`PUT /api/memories/owner`). The reflection's own lines go through the one gate for learned
  texts first ([The harness](harness.md#one-gate-for-every-text-dot-learns), item 587): one that holds a secret, a link,
  an e-mail address, a path in somebody's home folder, an account name or a character that does not print, one that
  starts like a heading of the prompt, and one that wears the mark "(you said)" (which the prompt says outranks the
  other lines) are dropped and logged, and the night goes on (a night in which every line is refused keeps the lines of
  the nights before, and says so in the log); so is one stored before that gate when the prompt is built. The section
  names your tools and sites by what they are called ("GitHub", "the BBC", "Linear"), and the reflection's writer is
  asked to: a line of Dot's that names a site by its domain (`bbc.co.uk`) or its address, an account by its @handle, or
  anyone by an e-mail address is dropped at night and left out of the prompt, as above, since every run reads these
  lines as instructions and a domain in one is a place to send a run (a name tells it the same). Your own lines are
  never checked, so a site you write by its domain there stands as you wrote it. Before them the section names you, as
  you gave your name in Settings › General ("(you said)") or as your account on this Mac has it, and, in Dot's own runs,
  the name you call Dot by, once it is not Dot (item 420); those lines come from Settings, not from the Memory page.
- The score decides too: over the last `score_days` (30), a note's balance is the runs handed it that ended done less
  the ones that failed. Once `score_min_uses` (3) runs were handed it, the balance lifts or lowers the note in a search
  as much as a fresh use would, and a negative balance rests it at once, before the nightly reflection retires it.
- A note saved or changed while a run works reaches that run at its next step, when the run would be handed it at its
  start: not another agent's lesson, not a resting note, and only as many as fit the budget of the starting notes (the
  run is told how many more changed; `memory_search` finds them). A change of tags alone, such as the nightly `rest` or
  merged tags, is no change: the note keeps its date, so an old fact retagged at night does not pass for newer than its
  correction.
- Three tags are the nightly reflection's: `journal` on the day's note, `rest` on a lesson no run was handed for sixty
  days, which searches skip (the list and the page still show it; take the tag off to wake it), and
  `keep-separate` on the notes whose merge you took back, which no night merges again (take it off to let them).
- The night merges notes that say the same in other words (item 584). Besides the fold of two lessons that say the
  same the same way round, which asks no model, the reflection shows the review model the notes of Dot's that are
  awake and not pinned, not yours and not a journal, whose embeddings are at least `merge_similarity` (0.80) close
  to each other, of one agent, both saved while reading outside content or neither, both saying yes or both saying
  no (a note and its correction stay for the outdated check, which now asks about the closest pairs first); it says
  which say the same thing and whose words say it best, at most `merges_per_night` (20) a night, the closest groups
  first. The groups of notes saved while reading outside content are asked about in a question of their own, apart from
  the others, so that no word a page put in a note is read beside a clean group, whose verdict it could try to steer;
  a note you pin or forget while the model answers keeps its group out of that night's merges (it is asked about
  again), and the rest of the night goes on. The merge is one new note with the words of that source (no model writes new words, so nothing a page put
  in a note is rewritten), the tags of all of them, their uses added up and the dates of the oldest and the newest,
  and the mark of outside content if any of them has it. Every source rests, whole, and names the merged note, so
  nothing is deleted; the merged note lists what it was made of (`merged_from`, with each source's run, dates, tags
  and uses). **Undo this merge** (`POST /api/memories/{id}/unmerge`, `dotx memory unmerge`) wakes the sources, tags
  them `keep-separate` and puts the merged note in the trash. A note you wrote, a lesson you saved, a pinned note, a
  `core` note and a journal are never merged, and the merges never touch What Dot knows about you. A chat that was
  handed a note that was merged away since is handed the merged note in its place and told the old one is gone.
- A lesson that says what the adopted harness's habits or tool guidance say already (they are in every prompt, so the
  lesson adds a line to the memory and nothing to a run) is not drawn, and one saved before the rule was adopted rests
  at night, unless you saved it or pinned it (item 584; [the harness](harness.md)). The night checks a lesson once for
  each version of the harness: it remembers which lessons it found to repeat no rule, and asks the embedding service for
  nothing on a night with no new or rewritten lesson and no new version.
- Nothing is forgotten without you (item 584). A note that has rested for `forget_after_days` (90; counted from its
  `rest` tag, or from its last use or write plus `rest_days` for one that rested before dates were kept) is proposed
  to you, never deleted: **Proposed to forget** and `dotx memory proposals` list them, the longest resting first,
  and your notes, the pinned ones (the standing ones among them) and the lessons you saved are never among them.
  **Forget** and **Forget all** (`POST /api/memories/proposals/forget`, `dotx memory forget --proposed`) name the
  notes by the ids the page showed, so a note that became a proposal after the page was drawn is not forgotten
  unseen; **Keep**
  (`POST /api/memories/proposals/keep`, `dotx memory keep`) wakes a note, which is proposed again only after as
  long a rest. A note you forget, from there or from any other Forget, goes to the trash for `trash_days` (30),
  whole with its embedding: `GET /api/memories/trash` and `dotx memory trash` list it (the newest 200, and say how
  many notes the trash holds in all), `POST /api/memories/trash/{id}/restore` and `dotx memory restore` bring it
  back awake, `DELETE /api/memories/trash/{id}` and `dotx memory purge` delete it at once, and the reflection
  deletes the notes that stayed longer, and none else. A note in the trash is in no search, list or count.
- `memory_update` lets the agent correct a note in place, and the note is then the agent's, even one you wrote: the
  prompt no longer shows the model's words as yours. You edit one on the Memory page, in a dialog its Edit opens (item
  615), or with `PATCH /api/memories/{id}`, and it keeps its author. The page also shows who wrote each note and how often runs used it.
- Search combines SQLite full-text search (BM25) with cosine similarity of embeddings and fuses both rankings, each
  cut to the best `candidate_pool` (30) notes among the ones that may be found: resting and expired notes, and the
  lessons of an agent in the search of another run, take none of those places (item 557).
  `embed_enabled = false` searches by words only. An answer of Ollama that says its model runner broke off is asked
  again, as a broken connection is; when the embedding service still fails at the start of a run, the run recalls its
  notes and lists its skills by their words alone and goes on, and the terminal and the side panel's Run tab, under Set
  up, say so in a warning line (`embedding_failed`, items 256 and 365). The word search does not mind case, accents or
  the Turkish dotted and dotless i (`toplanti` finds `Toplantı`), and a word of five letters or more also finds the
  words that start with its first five letters, so Turkish endings meet the root (`toplantıda`, `toplantılar`). The
  embeddings of the skills' texts are made as the daemon starts, in the background and 32 texts a call, and kept in the
  store across restarts (`help_vectors`, item 404); a run that finds more than 64 of them not made yet lists its skills
  by their words that once, without a warning, while they are made behind it. Before, the first run after each start
  waited for all of them in one call, half a minute and more, and one call with hundreds of texts broke Ollama's model
  runner.
- `dotx setup` writes `[memory] embed_enabled = false` when it sets up the models of a provider other than Ollama and
  the Ollama of this Mac does not serve the embedding model (item 558): `dotx doctor` then passes, and no run waits
  out an embedder that is not there. It says how to switch the embeddings on again.
- Saving a note close to an existing one (`similar_threshold`) tells the model, which decides whether the old note is
  outdated.
- A note a run saves while it has read content third parties can author is marked "saved while reading outside content"
  (the Memory page, `dotx memory list`), and a `memory_search` that returns one counts as reading such content again:
  the run is tainted, so its next save asks unless the approval mode answers that question itself, and the note it
  writes keeps the mark (`yolo` removes the approval, not the origin). Such a note is never among the notes a run
  starts with, is handed while it works or is handed as a site's lessons when it reaches the site, nor among the notes
  the model that writes lessons is shown (item 384): those are handed as trusted data, with no reader in between, so
  only `memory_search` finds it, and the start says in one sentence how many notes were left out so. A lesson drawn
  from such a run, and the reflection's journal that names it, carry the mark too; a lesson you save yourself does
  not. The same words saved again leave a note's mark as it was. Your own rewrite of a note, on the Memory page, takes
  the mark off: you vouch for its text. A note that holds an API key or one of the
  secrets Dot holds is refused, and the model is told to record where the key lives, not the key; the notes you write
  yourself are not refused.
- The default embedding model, `bge-m3`, is multilingual: English and Turkish notes are found by meaning (on a small
  hand-written set, the right note came first for 15 of 17 Turkish questions, against 10 with the English-centric
  `mxbai-embed-large`). `min_similarity` and `inject_min_similarity` are set for its scores. For another Ollama model
  set `[memory] embed_model`, run `dotx memory reindex` and adjust the thresholds to the scores of that model.
- To embed the notes with the OpenAI API instead, for example when the local model gives you trouble, add the key once
  and choose the provider. It is a choice, never a fallback: a failing provider is an error and the other one is not
  tried, and the text of every note and search then leaves this Mac.

  ```bash
  dotx auth set OPENAI_API_KEY
  ```

  ```toml
  [memory]
  embed_provider = "openai"
  embed_model = "text-embedding-3-small"
  min_similarity = 0.40
  ```

Then run `dotx memory reindex` (vectors of two models cannot be compared, so Dot also drops the old ones by itself).
`dotx doctor` shows whether the key and the model work. The lower `min_similarity` is needed: the scores of
`text-embedding-3-small` sit lower than those of `bge-m3` (a note's own question scores 0.38-0.79, other notes mostly
below 0.3), so the shipped `0.50` lets only part of the right notes through. Measured against the real OpenAI API on the
same small hand-written set (17 Turkish and 10 English questions over 37 notes; indicative, not a benchmark claim): at
`0.50` the right note came first for 9 of 17 Turkish questions, at `0.40` for 14 of 17 (16 of 17 within the first three,
English 10 of 10), where `bge-m3` gives 15 of 17. `inject_min_similarity = 0.55` and `similar_threshold = 0.85` held on
the measured pairs (no wrong note at 0.55; the same fact scored 0.85-0.94, different facts at most 0.66). A recall costs
about 0.19 s with OpenAI against 0.04 s with the local model, because every search is a network round trip.

## Schedules

```bash
dotx schedule add --name "Morning digest" --cron "0 9 * * 1-5" "Summarise what is new on the pages I follow and notify me"
dotx schedule add --name "Renew reminder" --at 2026-10-06T09:30 --mode research "Check whether the invoice portal lists a new bill"
dotx schedule list
dotx schedule disable|enable|delete ID
```

A schedule is a complete goal that Dot runs later, once (`--at`) or on a cron rhythm (`--cron`, five fields, read in
`[scheduler] timezone`, which defaults to the zone `TZ` names when it is set, else the zone of this Mac). Each firing
starts a run in a new chat, so the goal has to say everything; its first message carries, in front of the goal, the
dated final answers of the schedule's last ended runs (`[scheduler] history_runs`, 5; 0 for none; at most
`history_chars` characters, each answer cut at 600), marked as what the schedule found before and not as instructions,
so the run builds on them instead of doing the same work again (item 257). The chat shows the goal alone, and the run's
reviewer takes only the goal as your words; a one-time schedule that fires late says when it was due in that first
message too. With `same_chat` a firing goes on in the chat of the one before (a new one when you deleted it), where the
run reads their conversation instead of the digest. Either way it reads earlier runs' words without a message of yours,
so it starts tainted (asking before outward-facing actions) when one of the runs it reads ended so. The digest counts
the firings since item 257: a firing from before names no schedule. The agent can create schedules too
(`schedule_create`): such a schedule goes on in the chat it was made in, with that chat's choices as its composer shows
them (the ones its first run started with until you picked others: its mode, model, reasoning level, speed, approval
mode and goal mode; item 291). `/loop 30m <message>` in the box of a run or of a new chat makes such a schedule from the
composer (items 281 and 400; see [Runs](runs.md)); a chat with such a schedule wears its mark in the sidebar
(item 580). The scheduler also fires the nightly reflection at
`[agent] reflection_time` (see Reflection in [Autonomy](autonomy.md)); it is not a schedule and does not count against
`max_schedules`.

The **Morning brief** (item 570) is a schedule too, set on a card under your schedules (its **Settings** open in a
dialog, item 615) and labelled **Brief** in the list: a read-only run (`research`) at the time you set whose goal Dot
builds each time it fires, from what it knows of
itself, and whose first line goes to your phone. It counts against `max_schedules` while it is on, switches off in the
list and fires, skips and fails as any schedule does (see [Dot comes to you](proactive.md)). The **Watchers** panel
under it (item 571) is not a schedule: it looks at mail, calendar events and GitHub notifications and tells you of what
you care about.

On Schedules the list is the page and **New schedule** opens the form, in a dialog over the page (item 615), as a
schedule's pencil opens the same dialog filled; a schedule is created and edited
whole: its name, goal, repetition (cron or one moment), time zone, kind of run (task or research: a chat that only talks
schedules tasks), model, reasoning
level, approval mode, goal mode, the earlier runs it knows, whether it keeps its chat, when its failures switch it
off, how the end of its runs is told, the agent it runs as and whether it is on (`history_runs`, `same_chat`,
`stop_after_failures`, `notify`, `agent_id` and `speed` in `POST` and `PATCH /api/schedules`, left out for the
configured ones and the standard speed; a `PATCH` that leaves `agent_id` out keeps the schedule's agent, and `null`
makes it Dot's).
Options left on "default" take the configured value when the schedule fires. A schedule the agent creates takes its
chat's choices, which are yours: the model of a run cannot pick another approval mode for it, nor an agent; one made
in a chat with one of your agents runs as that agent (item 463), and one made in Dot's chat as Dot. The dialog has a
cross at its top right and Cancel at its foot; Escape closes it too, and asks first (*Discard your changes?*) when you
changed a field. A schedule the daemon refuses (a cron that fires every minute) is said inside the dialog, above its
foot, with what you typed still there; a form made anew for each schedule starts from the defaults, not from the last
one's choices. On a phone the dialog is a sheet of the window's width whose foot stays in view above the keyboard.

**Run now** (the play button of a schedule's row, `POST /api/schedules/<id>/run`, item 455) starts one of its runs at
once, switched on or off, due or not; the button says that it does real work, as at its time, with the approvals its run
takes, and the run's page opens, where Stop is. The run goes as a firing goes: unattended, with the schedule's own
choices (a yolo schedule's run asks nothing), the digest of its earlier runs, in a chat of its own titled `<name> ·
<time> · by hand` (or in the schedule's chat, with "the same chat"), and its first message says it was started by hand.
Nothing of when the schedule fires next nor of whether it is switched on changes: a one-time schedule is still due at
its moment. It does not start while the emergency stop is engaged, nor while a run of the schedule is at work or being
started (409), in a daemon that runs no scheduler either; a repeating schedule's firing whose time comes meanwhile is
skipped as for a run still going, and a one-time schedule waits, still due, and fires once that run has ended. A start
the daemon refuses leaves no chat behind and is no missed firing. **Firings** under a schedule lists its last runs, at
its time and by hand, newest first, one at work among them, each with how it went, when, its chat and the first line of
its answer (`GET /api/schedules/<id>/firings?limit=`, up to 50); a later firing's digest counts a run by hand among its
earlier runs.

- Schedules fire only while the daemon runs and the Mac is awake; `dotx service install` keeps the daemon running.
- A run of a schedule that the daemon's end cut off and you resumed (Resume on its page) counts as the schedule's run
  at work when the schedule comes due beside it (item 553): a repeating schedule is skipped, as for any run still
  going, and the end of that run is written to the schedule. One that ends before the schedule is due again writes
  nothing to it.
- A firing is at most once. One that is overdue by more than `misfire_grace_s` (the daemon was off, or the Mac slept)
  is skipped and waits for the next; you are told, as you are when a firing's run cannot start (see the notifications
  under the web UI). A one-time schedule has no next time and is never dropped: overdue, it fires late, and its run
  reads first when it was due and when it starts. One whose run could not start stays due and is tried again a minute
  later, then after twice as long each time, an hour at the most. One whose firing a crash cut before its run was under
  way is due again at the time it fired once the scheduler starts, unless its run had been made (item 386).
- The scheduler goes on past a failure: the schedules, the campaigns and the nightly reflection are each looked at on
  their own at every tick, one that fails (the store, a campaign's start) is logged, and the next tick tries again. Only
  20 failed ticks in a row (five minutes at the default `tick_s`) end the scheduler, and the daemon with it, with an
  error, so that schedules never stop firing unseen; the service that `dotx service install` sets up starts it again.
- A repeating schedule whose runs end failed so many times in a row is switched off, says so as its status ("switched
  off: its last 3 runs failed") and tells you once (item 458): **Switch off after** in its form (3, 5, 10, 20, never),
  else `[scheduler] stop_after_failures` (0, never, by default). Its row says how many failed in a row. A run done sets
  the row to 0, a run cancelled (stopped by you, the emergency stop or the daemon) counts neither way, a run started
  by hand counts as one at its time, and a firing that started no run counts not at all. A one-time schedule is never
  switched off by it. Switching a schedule on again, or changing it, starts its row afresh.
- How the end of a schedule's run is told on your phone and desktop (item 461): **Notifications** in its form, else
  `[scheduler] notify` (`always`, every end, by default). **Only when it fails or needs me** (`problems`) tells a run
  that ends with something it needs from you, failed or not, and of the other failed runs the first of a row, not the
  ones after it; **Never** (`never`) tells no end of its runs and no missed firing, which its row, its **Firings** and
  the run's page still show. A run started with **Run now** is told at every level, as are a question, an approval, a
  draft and the schedule's switch-off; a schedule deleted while its run works tells that run's end at the level it had.
- A schedule runs as Dot or as one of your agents (item 462): **Agent** in its form, Dot by default. Its runs carry the
  agent, as a chat with it does (see [Agents and campaigns](agents-and-campaigns.md)): its instructions, its tools only,
  its face on the row with an "As <name>" chip. Its model, reasoning level and approvals are the schedule's own where it
  chose them, else the agent's, else the configured ones: a yolo schedule of an agent that asks runs yolo, and one left
  on Default takes the agent's mode, which the form's Default names ("Default (the agent's: …)") and the row shows. An
  agent that does not exist is refused (404), and Dot is named by no agent (422). Deleting the agent switches its
  schedules off, as Dot's ("switched off: its agent was deleted"). While you have paused the agent (item 465), its
  schedules fire nothing by themselves: a repeating one is skipped to its next time ("skipped: <name> is paused") and a
  one-time one waits, still due ("waiting: <name> is paused"), and fires once you resume it; a pause counts no failed
  run and tells nothing, and **Run now** still starts a run. An agent that cannot be read fires nothing.
- A cron expression may not fire more often than every `min_interval_s`; at most `max_schedules` schedules can be on.
