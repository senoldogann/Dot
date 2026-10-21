# Memory and schedules

## Memory

Dot keeps short notes between runs: facts about you, your machine and how you like things done. `memory_save`,
`memory_search` and `memory_forget` are tools of the agent; you manage the same notes with `dotx memory` and in the web
UI, where Settings › Memory is one column of cards: the box that adds a note, then the notes changed last first, 30 at a
time with **Load more** for the next and their search in the head of the list (item 373), then what Dot knows about you
with your own lines, then how learning fares; a note's actions show when the pointer is on its row (always on a touch
screen); forgetting, correcting or adding a note keeps the notes read and the list where it was, and a correction open
on another note stays open (item 204). In a run's conversation, a note the run saves, corrects (`memory_update`) or
forgets leaves a quiet line where it happened, what changed and the first words of the note: "Saved to memory", "Updated
memory" or "Removed from memory" (item 313, after Grok's "Updated memory"). The line of a saved or corrected note opens
Settings › Memory with that note first, to read, correct or forget (`#/settings/memory?note=ID`, `GET
/api/memories/{id}`); a note forgotten since is said to be gone. A note saved again, or a call that failed, changed
nothing and leaves no line. The line names no agent: a note a run saves is Dot's, for every run (item 94). The notes a
run is handed at its start leave a line too, "Recalled 3 memories", not in the conversation but under Set up in the side
panel's Run tab (item 365), and only when its chat had not been handed one of them before: a follow-up handed the same
notes says nothing, and one handed a new note says how many are new, "(1 new)" (item 335). The notes themselves go under
the message that recalled them, in the note that gives the time ("[system note] It is now…", item 404), not into the
instructions: the instructions stay the same bytes from one message of a chat to the next, so the provider serves them
and the history from its prompt cache (the first word of an answer came after 30 to 50 seconds of a 200,000-token chat
before; `first_token_seconds` on each `model_call` event measures it from now on); a note an earlier message of the chat
was handed is not repeated while its text stands, comes again when it changed (a correction on the Memory page, a run's
update), and is named as forgotten when it was, since the old text stays in the chat; a note folded into a summary, in
the run or since, is handed again after the summary, as it stands then.

How learning fares, the last card, says for the reflection, the lessons and the skills when each last went well and,
for one that failed, how many times in a row and why. A work with neither a success nor a failure recorded (one that
had nothing to learn from leaves no mark) says "nothing recorded yet", not that it has not gone well.

```bash
dotx memory add --tag core "I prefer answers in Turkish, with code and commands in English"
dotx memory search --limit 5 how do I like my answers
dotx memory list
dotx memory forget NOTE_ID
dotx memory reindex      # embed every note again, for example after changing [memory] embed_model
```

- A run starts with the notes tagged `core` (newest first), then the notes you wrote yourself (the ones closest to the
  goal first), then the agent's notes close enough to the goal and the earlier goals of the conversation
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
  still finds it, but runs no longer start with it on its own. Your notes and the `core` ones never rest that way.
- A note can hold for a while (item 306): `memory_save` takes `valid_days` for a fact that is true only so long (a
  trip, a deadline, an offer). The model is told until when it holds ("holds until 2026-10-20"); once that day passes
  the note rests, whoever wrote it, and neither a run nor a search is handed it; the Memory page says "Holds until …",
  then "Ran out …". Saving the same note again wakes it with the new time. Under each note the page says how the runs it was handed to
  ended ("Handed to 5 runs, 4 done"); every use is recorded with the run's end, which `dotx stats` reads.
- What Dot knows about you opens the memory part of every prompt: a short section the nightly reflection keeps (your
  language, hours, tools and sites, the ways you ask, the limits you set), rewritten each night from its previous
  version, your notes and the day's runs. The Memory page shows it first; the lines you write under it are yours, kept
  as they are, marked "(you said)" in the prompt, never rewritten, and the reflection never contradicts them
  (`GET`/`PUT /api/memories/owner`). Before them the section names you, as you gave your name in Settings › General
  ("(you said)") or as your account on this Mac has it, and, in Dot's own runs, the name you call Dot by, once it is
  not Dot (item 420); those lines come from Settings, not from the Memory page.
- The score decides too: over the last `score_days` (30), a note's balance is the runs handed it that ended done less
  the ones that failed. Once `score_min_uses` (3) runs were handed it, the balance lifts or lowers the note in a search
  as much as a fresh use would, and a negative balance rests it at once, before the nightly reflection retires it.
- A note saved or changed while a run works reaches that run at its next step, when the run would be handed it at its
  start: not another agent's lesson, not a resting note, and only as many as fit the budget of the starting notes (the
  run is told how many more changed; `memory_search` finds them). A change of tags alone, such as the nightly `rest` or
  merged tags, is no change: the note keeps its date, so an old fact retagged at night does not pass for newer than its
  correction.
- Two tags are the nightly reflection's: `journal` on the day's note, `rest` on a lesson no run was handed for sixty
  days, which searches skip (the list and the page still show it; take the tag off to wake it).
- `memory_update` lets the agent correct a note in place, and the note is then the agent's, even one you wrote: the
  prompt no longer shows the model's words as yours. You edit one on the Memory page or with `PATCH /api/memories/{id}`,
  and it keeps its author. The page also shows who wrote each note and how often runs used it.
- Search combines SQLite full-text search (BM25) with cosine similarity of embeddings and fuses both rankings.
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
composer (items 281 and 400; see [Runs](runs.md)). The scheduler also fires the nightly reflection at
`[agent] reflection_time` (see Reflection above); it is not a schedule and does not count against `max_schedules`.

On Schedules the list is the page and **New schedule** opens the form; a schedule is created and edited
whole: its name, goal, repetition (cron or one moment), time zone, kind of run (task, chat, research), model, reasoning
level, approval mode, goal mode, the earlier runs it knows, whether it keeps its chat and whether it is on
(`history_runs`, `same_chat` and `speed` in `POST` and `PATCH /api/schedules`, left out for the configured ones and the
standard speed). Options left on "default" take the configured value when the schedule fires. A schedule the agent
creates takes its chat's choices, which are yours: the model of a run cannot pick another approval mode for it. While
the form is open, **New schedule** reads **Close**, with a cross.

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
- A cron expression may not fire more often than every `min_interval_s`; at most `max_schedules` schedules can be on.
