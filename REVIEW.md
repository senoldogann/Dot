# Review guide

Read by `/code-review` and Code Review for Claude Code. Dot runs with the owner's rights on their Mac: a change is judged
by what it lets a run do as much as by how it reads.

## What failure looks like here

- A run does something the policy should have stopped: it pays, enters a credential, posts, sends a message, changes a
  system setting or escapes its sandbox without the approval its mode requires.
- A secret reaches a model, the logs, the events, the API or the web UI unmasked.
- The daemon's API answers a request it should refuse: a missing or wrong token, a request from somewhere it shouldn't
  come from.
- A run says "done" when the workspace shows it isn't, or an action is not recorded and can't be undone.
- A page under `docs/` says Dot does something it no longer does.

## Always flag (Important)

- Behavior that contradicts the PR's stated intent; a bug fix that patches a symptom instead of the root cause
- An error swallowed, caught by a catch-all, or hidden behind a fallback
- Data from outside (a file, a service's answer, a web page, a tool's output) used before it is validated into a model
- A test that calls a model or reaches outside this Mac without a `live`, `internet` or `hosted` mark, or that touches
  the real `DOT_HOME`
- A change in what Dot does without the matching change under `docs/`
- A change that makes a line in `CLAUDE.md`, `CONTRIBUTING.md` or `docs/` stale
- A change that makes a yolo or free run ask the owner something it did not ask before, or that denies in those modes what
  it allowed: the owner's freedom is a contract ([docs/freedom.md](docs/freedom.md)), and such a change is Important

## Checks made from reviews

A finding that comes back gets a check, not another comment: a test or a gate step that fails the next time. Put the
check in the same pull request as the finding, and name it here. `tests/unit/test_review_rules.py` fails if a line
here points at a test that no longer exists.

- `tests/smoke/test_phone_keys.py::test_a_check_that_began_before_a_change_records_nothing` — a check of the old key that
  ended after a new key was saved showed its verdict against the new one (item 2054's security review).
- `tests/smoke/test_chat_routing.py::test_a_routed_message_is_checked_as_a_queued_one_and_move_keeps_what_it_cannot_move` — words the router queued for a task skipped a queued message's checks, and Move took out a message it could not
  move (item 2012's review).
- `tests/smoke/test_chat_routing.py::test_a_move_that_fails_loses_nothing` — a move that failed after the message was
  taken out lost it, and a run was stopped before its move was checked (item 2012's review).
- `tests/smoke/test_stopped_run_text.py::test_an_emergency_stop_keeps_the_words_too_and_says_stopped_in_turkish` — a run the emergency stop ended kept the daemon's English sentence and lost its streamed words (item 1980's review).
- `tests/smoke/test_scheduler.py::test_the_emergency_stop_cancels_the_kind_pass_under_way_and_starts_none` — the
  hourly pass that asks the fast model the kind of a note went on under the emergency stop (item 1975's review).
- `tests/smoke/test_work_orders_page.py::test_a_next_step_written_after_outside_content_is_marked` — the Agents page and the
  phone's bot profile showed an objective's next step with no outside-words mark: the step has its own flag, which the mark
  left out (item 2009's review).
- `tests/unit/test_first_words.py::test_a_fault_of_the_first_words_is_logged_not_lost` — the first words are a task
  nothing awaits, so a fault that was no error of Dot's was dropped with no line in the log (item 2018's review).
- `tests/smoke/test_agents.py::test_the_first_meeting_of_an_agent_with_its_own_reasoning_level_still_starts` — the welcome ran
  on the fast model with the agent's own reasoning level, which that model need not offer: the meeting was refused and the
  chat deleted (item 1962's review).
- `tests/unit/test_css_tokens.py::test_the_ink_on_dots_warm_backdrop_reads_on_every_stop_of_it_in_both_looks_and_both_schemes` — the
  hint and the title on Dot's computer's amber backdrop took the page's ink, light in dark: 1.37:1 (item 1324's review).
- `tests/unit/test_css_tokens.py::test_no_text_is_set_in_the_accent_which_is_a_fill_colour` — six rules still set text in
  `--accent` after it became a fill colour: 4.06:1 on the dark surfaces (item 1325's review).
- `tests/unit/test_css_tokens.py::test_the_focus_ring_is_the_solid_accent_and_reads_at_3_to_1_on_every_surface` — the
  focus ring was the accent at 60 %, 2.2:1 in dark, under the 3:1 of a focus indicator (item 1325's review).
- `tests/unit/test_css_tokens.py::test_the_docs_name_the_easing_the_sheets_use_and_not_an_older_one` — `docs/daemon.md` still
  told the springy easing, a 96 % menu and a 260 ms sheet after the look changed them (item 1326's review).
- `tests/unit/test_css_tokens.py::test_a_phone_draws_no_text_token_under_13_px` — the phone's two metadata sizes were 11 and
  12 px under the 13 px the sheet and the docs promise there (item 1324's review).
- `tests/unit/test_css_tokens.py::test_no_sheet_comment_or_doc_lists_what_is_round` — the token's comment, the design
  page, the daemon page and the item's record each listed the round things and no two agreed (item 1324's review).
- `tests/unit/test_css_tokens.py::test_the_daemon_page_tells_the_conversation_once_and_with_no_bubble_on_a_phone` — a
  rebase left `docs/daemon.md` with two copies of the conversation paragraph, one from before the new look (an 18 px
  corner, a quiet ring) and one that still said Dot's words sit in a soft bubble on a phone (item 1335's review).
- `tests/smoke/test_work_awake.py::test_a_work_check_that_keeps_failing_lets_a_hold_run_out` — a work check that raised kept the Mac awake with no end, and logged a traceback every tick
- `tests/unit/test_css_tokens.py::test_every_comment_of_a_sheet_closes_where_it_opens` — a merge left a line of a comment under one that had closed it, so the browser read the words as a selector and dropped the rule after it (item 1335).
- `tests/smoke/test_panel_automations.py::test_the_task_tab_lists_the_chats_schedules_and_loops_and_switches_and_runs_them` — the
  pencil of a chat's Automations linked to `#/settings/schedules/<id>`, which no route matches, so a press dropped the
  owner on New chat while the docs said the editor opened; the test follows the pencil to the open editor (item 1334; the
  address is a route since item 1337, and the test reads the editor's Name field).
- `tests/smoke/test_browser_chat_cards.py::test_the_daemon_shows_cards_at_desktop_and_phone_widths_and_replays_them` — a
  choice card sent its words as the owner's message on one tap, so a run that had read a hostile page could word an
  option that started a run unmarked; a tap now only writes the words into the composer, as a page's choice does, and the
  test checks that no run starts until the owner sends (item 1967's review).
- `tests/smoke/test_schedule_agent.py::test_every_way_out_of_the_editor_asks_not_the_links_alone_and_the_browsers_back_is_put_back` — the
  editor's "Discard your changes?" guard listened for clicks on links, so the nav search's Enter, the shortcuts and
  the browser's own Back dropped what was typed silently; the router now asks the page before any page swap, and puts
  a refused change back (item 1337).
- `tests/smoke/test_schedule_agent.py::test_the_phones_back_button_and_connect_apps_ask_before_the_editor_drops_what_was_typed` — the same
  guard missed the phone's Back button in the bar (`history.back()` and `location.replace`) and Connect apps, so a
  phone dropped the form on almost every way out of the editor (item 1337).
- `tests/smoke/test_oauth_accounts.py::test_a_refused_revoke_keeps_the_token_to_ask_again_and_the_sign_out_stands` — a
  sign-out forgot the tokens before the provider's revoke, and a refusal was only logged (its error dropped), so the
  provider kept listing Dot and the page said "signed out" (item 1312).
- `tests/smoke/test_phone_limits.py::test_a_foreign_push_host_is_refused_and_a_removed_phone_takes_its_subscriptions` — a web
  push subscription took any https address, so Dot posted every push, signed, to a host the caller named, and it
  belonged to no phone, so removing the phone left it (item 1311).
- `tests/unit/test_push_hosts.py::test_no_packaged_entry_takes_a_domain_that_serves_what_anyone_hosts` — the first list
  took `*.googleapis.com/fcm/`, which `<bucket>.storage.googleapis.com/fcm/...` matches, so a caller still named where
  Dot posts a push (item 1311).
- `tests/smoke/test_stream_reread.py::test_events_of_a_run_that_reach_the_bus_late_are_read_from_its_own_cursor_not_the_newest_id_of_another_run` — a
  stream re-read from one cursor for every run, so the events of a `dotx` run, relayed late, were skipped for good
  (item 1313).
- `tests/unit/test_store_schema.py::test_a_copy_that_meets_a_full_disk_says_the_disk_is_full` — the copy made before
  an upgrade told a full disk only from the system's error, while SQLite's online backup, its largest write, reports
  it as its own (item 1268).
- `tests/smoke/test_context_budget.py::test_a_tool_the_run_let_go_is_not_named_and_not_callable` — the fit ran after
  the groups, the registry and the prompt were built, so a run named tools it had let go, and a dropped tool stayed
  callable (item 1211).
- `tests/unit/test_eval_plan_runs.py::test_a_plan_run_that_had_dot_read_a_page_counts_its_steps_and_that_read` — a
  plan run with one side call of Dot's was measured from that call alone, its steps dropped (item 1220).
- `tests/unit/test_tool_call_origin.py::test_another_models_turn_is_not_handed_a_signature_it_never_wrote` — a tool
  call's replay state went back to whatever model the conversation was sent to, not only the one that wrote it
  (item 1228).
- `tests/unit/test_antigravity_request.py::test_a_revoked_login_says_to_sign_in_again` — a token endpoint answering 400
  (a revoked login) was read as a protocol error, and nothing else noticed the login was gone (item 1228).
- `tests/unit/test_antigravity_request.py::test_the_answers_to_two_calls_of_one_turn_are_one_content` — the answers to
  two tool calls of one turn went to Gemini as two contents, so a call was left unanswered (item 1228).
- `tests/unit/test_antigravity_request.py::test_a_token_endpoint_having_a_bad_moment_is_asked_again` — a 5xx from the
  token endpoint was reported as a refused login (item 1228).
- `tests/unit/test_doctor_rows.py::test_a_models_row_is_named_for_its_own_provider` — Google's models row in the checks
  was called "openai api" (item 1228).
- `tests/smoke/test_work_status.py::test_the_line_says_the_run_waits_for_you_only_until_the_step_is_answered` — the
  line of the work kept saying "Waiting for you" after the owner's yes, while the step's command ran (item 1148).
- `tests/unit/test_continuity.py::test_the_note_names_a_command_the_owner_never_wrote` — a compaction's note carried a
  command a page put there into the run as its own task, and nothing read the note once it existed (item 1219).
- `tests/unit/test_continuity.py::test_a_command_sharing_only_a_tool_word_with_the_task_is_still_named` — the first cut
  compared a command's first word, so a task that said "with curl" made a planted curl the owner's (item 1219).
- `tests/unit/test_continuity.py::test_the_note_leaves_a_command_the_plan_carries` — the run's plan was never consulted,
  so a command it wrote itself would have been named as content read from outside (item 1219).
- `tests/unit/test_continuity.py::test_the_note_shows_the_newest_commands_and_says_what_it_left_out` — the cap kept the
  earliest commands and hid the one in NEXT, the command the run was about to follow (item 1219).
- `tests/unit/test_continuity.py::test_the_commands_section_stands_outside_the_summary_a_new_compaction_writes` — the
  section sat inside the model-written part, so a later compaction summarised the guard as the run's own story (item
  1219).
- `tests/unit/test_continuity.py::test_a_carried_command_keeps_the_quoting_it_was_written_with` — the clause cut stopped
  inside a command's own quotes, so `bash -c 'echo a; echo b'` was shown cut and could not run (item 1219).
- `tests/unit/test_continuity.py::test_an_apostrophe_in_prose_does_not_swallow_the_rest_of_the_line` — an apostrophe
  mid-word opened a quote that never closed, so the entry carried the rest of the sentence (item 1219).
- `tests/unit/test_compaction_taint.py::test_the_compaction_event_carries_the_mark_so_a_resumed_run_starts_untrusted` — the
  mark lived only in the gate, so a run cut off after a compaction and resumed started untainted (item 1219).
- `tests/unit/test_continuity.py::test_a_command_the_summary_opened_inside_a_quote_stops_at_its_backtick` — a command
  a summary wrapped in backticks swallowed the prose after it (item 1219, found on a live compaction).
- `tests/unit/test_continuity.py::test_a_quote_straight_after_a_word_still_holds_its_semicolon` — a quote that opens
  straight after a word was taken for prose, so the command was cut at its own ``;`` (item 1219).
- `tests/unit/test_compaction_taint.py::test_a_compaction_puts_the_steps_after_it_under_the_check` — a run that went on
  from a compaction held no step under the review model's check (item 1219); its sibling,
  `test_a_compaction_leaves_what_the_run_saves_since_its_own`, holds that its saves stayed its own.
- `tests/unit/test_browser_readback_calls.py::test_a_page_that_never_answers_ends_the_readback_at_the_steps_time` — a
  script that waits in the page was given Playwright's `timeout`, which is for finding the element only, so a frozen
  page held a batch without end (item 1151).
- `tests/unit/test_noted_yes_words.py::test_a_yes_with_a_note_is_no_refusal_to_the_model` — a yes with a note
  was told to the model as a refusal ("do not retry") while asking it to do the step again (item 1140).
- `tests/unit/test_noted_yes_words.py::test_a_batched_step_says_the_owner_is_asked_once_more` — a batched browser
  or desktop step told the run its noted yes would go through, while the batch's own page read let it go (item 1140).
- `tests/unit/test_apns.py::test_the_apns_key_is_guarded_from_the_runs_once_it_is_set` — the APNs key of
  `[apns] key_path` was said to be sealed from the runs and was not (item 997).
- `tests/smoke/test_secret_fields.py::test_ask_mode_types_a_one_time_code_the_user_gave` — a one-time code the user
  gave is typed and the form submits, while a code found on the page is refused (`test_ask_mode_refuses_to_type_a_one_time_code`).
- `tests/unit/test_ask_user_notify.py::test_a_left_question_notifies_the_user` — a question left for the user sends a
  notification.
- `tests/unit/test_limits.py::test_a_run_has_a_default_ceiling_and_zero_turns_it_off` — a run has a default
  120-minute, 10,000,000-token ceiling, and 0 turns a bound off.
- `tests/smoke/test_chat_permits.py::test_a_permit_never_answers_money_nor_a_step_of_dots_own_initiative` — a
  "don't ask again in this chat" never answers money nor a step of Dot's initiative, and answers one of a run that read
  outside content, as the owner chose (item 866).
- `tests/smoke/test_watch_video.py::test_a_calls_download_folder_is_its_own_and_its_removal_follows_no_link` — the
  daemon removed old download folders under the temporary folder, where a run could leave a link to the owner's home
  (item 1212): a call's folder is its own now, and its removal follows no link.
- `tests/unit/test_video_watch.py::test_a_track_name_that_is_no_language_tag_is_dropped_as_the_answer_is_read` — a
  caption track's name came from the page (`<track srclang>`) and reached Dot's own line, an error and a file's path
  (item 1212): a name that is no language tag is dropped as yt-dlp's answer is read.
- `tests/smoke/test_watch_video.py::test_what_a_site_says_when_a_look_fails_is_fenced_and_marks_the_run` — yt-dlp's
  error, which carries a site's words, reached the model unfenced and left the run trusted (item 1212).
- `tests/unit/test_video_watch.py::test_the_line_says_how_the_frames_were_chosen` — the line that tells the model how
  a look chose its frames was guessed from counts and said the wrong thing when quick cuts spread them (item 1214).
- `tests/smoke/test_pipeline_tools.py::test_a_ledger_a_run_that_read_outside_content_wrote_is_fenced_and_marks_its_reader` — ledger cells
  a reply's run wrote reached clean runs unmarked (item 1227).
- `tests/smoke/test_pipeline_tools.py::test_a_refusal_that_quotes_a_cell_of_outside_words_is_fenced_and_marks_its_reader` — the same
  cells came back unmarked inside a refusal that quoted them (item 1227).
- `tests/unit/test_objectives.py::test_a_next_step_a_run_wrote_after_reading_outside_content_is_counted_not_quoted` — a
  next step a reply's run set reached the look and the brief as the owner's plan (item 1227).
- `tests/unit/test_pipeline_ledger.py::test_a_third_partys_words_holding_a_marker_cannot_end_the_block` — a cell holding
  the block's end marker broke every later read of the ledger, and the send guard with it (item 1227).
- `tests/unit/test_pipeline_ledger.py::test_a_status_or_state_edited_into_none_of_the_ledgers_is_not_used` — a status of
  `Paused` slipped past a hold and a state of `Sent` let a sent mail be begun again (item 1227).
- `tests/smoke/test_pipeline_record.py::test_a_lock_file_that_is_a_link_is_refused_and_nothing_is_written` — the pipeline
  tools followed links on the path the call was allowed (item 681's guard, item 1227).
- `tests/unit/test_shell_guard_zsh.py::test_what_zsh_runs_of_a_line_the_guard_reads_too` — the shell guard read six
  ways of zsh's (`+=`, a comment in `$(...)`, `cd OLD NEW` and `cdpath`, a C shell, braces in the command's place, an
  alias) as safe while zsh read `~/.ssh` or deleted in the home folder (K1-01 to K1-06, item 1250).
- `tests/smoke/test_taint_check.py::test_a_step_too_long_for_the_review_model_to_read_is_refused_with_how_to_go_on` — a
  step past what the review model reads ran unread in a tainted yolo run (K3-01, item 1251).
- `tests/unit/test_review_target.py::test_a_schedules_whole_goal_is_read_not_its_middle_cut` — the review read a
  schedule's goal cut in the middle, and an MCP call's arguments cut at 300 characters (K3-02, item 1251).
- `tests/unit/test_pixel_click.py::test_a_part_of_a_button_presses_the_button` — a click at a pixel was judged by no
  element, so a payment button held back by its ref was pressed by its pixel (B2-01, item 1252).
- `tests/unit/test_css_tokens.py::test_no_font_size_multiplies_a_token_by_a_number` — the sidebar badge's letters were
  `calc(var(--text-xs) * 0.75)`, 9 px on a Mac and under the phone's 13 px floor, a size no token names that the
  literal-size rule did not see (item 1327's review).
- `tests/unit/test_check_item_records.py::test_a_commit_that_writes_another_items_records_is_an_offence` — a branch
  renumbered to the number main had just taken wrote over that item's changelog and roadmap, twice in one pull request
  (item 1265, the gate's `scripts/check_item_records.py`).
- `tests/unit/test_shell_guard_git.py::test_the_file_tools_ask_for_what_git_runs_and_for_nothing_else_in_a_repository` — a
  file tool wrote a repository's `config` or `hooks` as `low`, and a glob in the `.git` part of the path
  (`proj/.gi?/config`) was read as a different folder (item 1301).
- `tests/unit/test_shell_guard_git.py::test_git_given_a_place_to_write_or_a_line_to_run_is_judged_by_it` — `_assess_git`
  ended at the subcommand, so `git rebase --exec`, `bisect run`, `submodule foreach`, `filter-branch --*-filter`,
  `grep -O` and `diff --output` ran or wrote anything as `low` (item 1301).
- `tests/unit/test_shell_guard_programs.py::test_a_command_line_a_program_is_handed_is_judged_as_it_is_under_bash` — a
  line handed to `sqlite3 .shell`, `vim -c '!CMD'`, `emacs --eval` or `tar --use-compress-program` was `low` while the
  same line under `bash -c` was `high` (item 1302).
- `tests/unit/test_shell_guard_awk.py::test_a_pipe_into_a_shell_is_read_through_the_wrappers_in_front_of_it` — the
  check for an awk pipe into a shell read the first word of the command only, so `print | "env sh"` ran what awk
  printed while `print | "sh"` asked (item 1303).
- `tests/unit/test_model_refusal.py::test_an_agent_a_campaign_and_a_queued_message_are_judged_by_the_model_they_would_run_on` — the
  first check covered `start_picked` and the queue's default model only, so a message to an agent, Keep going or a team, and an
  agent's own message in the queue, still started and failed with "missing API key" (item 1350's review).
- `tests/unit/test_log_rotation.py::test_a_line_that_cannot_be_encoded_or_written_leaves_the_thread_writing_the_next_and_the_exit_returns` — the
  log's writer thread ended on a line it could not encode (a lone surrogate), and every later line was lost and the exit
  waited on a full queue (the pre-push review of item 1944).
- `tests/unit/test_review_instructions.py::test_a_short_restriction_behind_a_long_newest_message_is_still_read` — a long
  newest message, read whole, left a short "never email anyone" out of yolo's check of a tainted run, which asks nobody
  (the security review of item 1947).
- `tests/unit/test_safari_tools.py::test_an_open_never_loads_over_a_tab_of_the_owners_and_reuses_only_its_own_where_it_left_it` — `safari_open`
  took the chat's tab by its place alone, so a first open, or one after a read of the owner's front tab, loaded over
  the page the owner had there (item 1959's review).
- `tests/unit/test_safari_tools.py::test_a_page_whose_answer_cannot_be_read_is_not_quoted_to_the_run` — an answer of
  the page script that failed to validate, and an error a page made it throw, were put in the error, which reaches the
  model unfenced and leaves the run trusted (item 1959's review, the class of item 1212).
- `tests/unit/test_freedom_contract.py::test_no_call_of_any_kind_asks_the_owner_but_what_the_contract_names` — the owner's
  word of 2026-11-24 (yolo and free mode never break): a call of any kind of tool, prepared by its own tool, must go
  through unasked in a yolo or free run, but for the payment, the message or mail and the post the contract names, and a
  step the policy would have asked about is recorded with why (item 1561).
- `tests/unit/test_freedom_contract.py::test_every_tool_of_the_registry_has_a_row_or_says_why_it_has_none` — a tool added
  without a row of the freedom table could bring an ask into yolo and free mode unseen (item 1561).
- `tests/unit/test_freedom_contract.py::test_the_owner_is_asked_in_the_places_the_contract_names_and_no_others` — a tool that
  calls the approval broker itself asks the owner whatever the gate waives, in yolo and free mode too: a new place is a
  new ask (item 1561).

## Evidence and format

- Cite file and line for every finding; read the code instead of guessing from names
- Tag each finding **Important** (introduced by this change, must fix), **Nit** (optional) or **Pre-existing** (was
  already there; don't block on it)
- At most 3 nits; skip what `ruff format`, `ruff check`, `mypy`, `cargo fmt` and `clippy` already enforce
- When a bug has siblings, name the bug class and list every instance
- Start the summary with the finding count, e.g. "2 Important, 1 Nit"; with nothing to fix, say "No issues found"
- A pull request that carries several items (a commit `Item N: ...` each): name the item of every finding, and check
  each item's entry in `roadmap/` (and `changes/`, when what Dot does changes) against its own commit

## Re-reviews

- After the first full pass, review only the new diff
- Don't repeat findings that were fixed or answered with a reason
- After 3 rounds, stop and ask the author to reconsider the approach (the hook counts the rounds and says so; a push
  that only brings main in is not a round)

## Security review brief

A change under "Always needs a human" is also read by an independent agent that knows only the branch. Start it at the
first push, beside the review and the gate, and take its findings in the same push as the review's. The brief: the
worktree it may read; what it must not do (edit, commit, push, write outside a `mktemp -d` folder, feed a shell a
destructive line: probes go in files); the change in a paragraph; and what a finding is, an input that gets through with
the verdict it gets and its twin's, while a limit the docs already state is not one. It reports findings or says plainly
that it found none, and what it tried.

## Skip

- `uv.lock`, `desktop/src-tauri/Cargo.lock`, `desktop/package-lock.json`
- `docs/images/`, `desktop/icons/`, `desktop/src-tauri/icons/`
- `ROADMAP.md`, `roadmap/`, `CHANGELOG.md`, `changes/` (check only that the entry matches the change)

## Always needs a human

- `src/dotagent/policy/` (the policy, the shell guard, the sandbox, pre-approved spending)
- `src/dotagent/agent/approvals.py`, `src/dotagent/agent/review.py`
- `src/dotagent/secrets.py`, `src/dotagent/server/guard.py`, `src/dotagent/server/pairing.py`,
  `src/dotagent/server/routes_keys.py`
- `src/dotagent/undo/`
- `desktop/src-tauri/capabilities/`, `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/build.rs` (the commands the app manifest names, each behind a permission of its own)
- `desktop/src-tauri/src/installer.rs` (what the installer runs and with what environment, never over another Dot)
- `desktop/tools.lock` (the programs the app carries, pinned by hash)
- `desktop/fetch-tools.sh` (how those programs are fetched, checked and signed)
- `desktop/release.sh` (what a release builds, signs and publishes, and in what order)
- `pyproject.toml` dependencies
