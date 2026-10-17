# Memory and schedules

## Memory

Dot keeps short notes between runs: facts about you, your machine and how you like things done. `memory_save`,
`memory_search` and `memory_forget` are tools of the agent; you manage the same notes with `dotx memory` and in the web
UI, where Settings › Memory lists the notes changed last first, 30 at a time with **Load more** for the next, in a
box that keeps its size and scrolls inside past its first notes (item 218); forgetting, correcting or adding a note
keeps the notes read and the list where it was, and a correction open on another note stays open (item 204). In a
run's conversation, a note the run saves, corrects (`memory_update`) or forgets leaves a quiet line where it happened,
what changed and the first words of the note: "Saved to memory", "Updated memory" or "Removed from memory" (item 313,
after Grok's "Updated memory"). The line of a saved or corrected note opens Settings › Memory with that note first,
to read, correct or forget (`#/settings/memory?note=ID`, `GET /api/memories/{id}`); a note forgotten since is said to
be gone. A note saved again, or a call that failed, changed nothing and leaves no line. The line names no agent: a
note a run saves is Dot's, for every run (item 94). The notes a run is handed at its start leave a line too, "Recalled 3
memories", but only when its chat had not been handed one of them before: a follow-up handed the same notes says
nothing, and one handed a new note says how many are new, "(1 new)" (item 335).

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
  it started with; the timeline says "Recalled 2 memories learned on www.amazon.com". The run's
  timeline says how many notes it recalled and the tokens they took.
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
  (`GET`/`PUT /api/memories/owner`).
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
  notes and lists its skills by their words alone and goes on, and the timeline and the terminal say so in a warning
  line (`embedding_failed`, item 256). The word search does not mind case, accents or the Turkish dotted and dotless i
  (`toplanti` finds `Toplantı`), and a word of five letters or more also finds the words that start with its first
  five letters, so Turkish endings meet the root (`toplantıda`, `toplantılar`).
- Saving a note close to an existing one (`similar_threshold`) tells the model, which decides whether the old note is
  outdated.
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
run reads their conversation instead of the digest. Either way it reads earlier runs' words without a message of
yours, so it starts tainted (asking before outward-facing actions) when one of the runs it reads ended so. The digest
counts the firings since item 257: a firing from before names no schedule. The agent can create schedules too
(`schedule_create`): such a schedule goes on in the chat it was made in, with that chat's choices as its composer shows
them (the ones its first run started with until you picked others: its mode, model, reasoning level, speed, approval
mode and goal mode; item 291). The scheduler also fires the nightly reflection at `[agent] reflection_time` (see Reflection
above); it is not a schedule and does not count against `max_schedules`.

On Settings › Schedules a schedule is created and edited whole: its name, goal, repetition (cron or one moment), time
zone, kind of run (task, chat, research), model, reasoning level, approval mode, goal mode, the earlier runs it knows,
whether it keeps its chat and whether it is on (`history_runs`, `same_chat` and `speed` in `POST` and `PATCH
/api/schedules`, left out for the configured ones and the standard speed). Options left on "default" take the
configured value when the schedule fires. A schedule the agent creates takes its chat's choices, which are yours: the
model of a run cannot pick another approval mode for it.

- Schedules fire only while the daemon runs and the Mac is awake; `dotx service install` keeps the daemon running.
- A firing is at most once. One that is overdue by more than `misfire_grace_s` (the daemon was off, or the Mac slept)
  is skipped and waits for the next; you are told, as you are when a firing's run cannot start (see the notifications
  under the web UI). A one-time schedule has no next time and is never dropped: overdue, it fires late, and its run
  reads first when it was due and when it starts. One whose run could not start stays due and is tried again a minute
  later, then after twice as long each time, an hour at the most.
- The scheduler goes on past a failure: the schedules, the campaigns and the nightly reflection are each looked at on
  their own at every tick, one that fails (the store, a campaign's start) is logged, and the next tick tries again. Only
  20 failed ticks in a row (five minutes at the default `tick_s`) end the scheduler, and the daemon with it, with an
  error, so that schedules never stop firing unseen; the service that `dotx service install` sets up starts it again.
- A cron expression may not fire more often than every `min_interval_s`; at most `max_schedules` schedules can be on.
