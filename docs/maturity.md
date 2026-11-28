# Maturity

Which of Dot's features were last proven to work on the owner's real Mac, when, and how (item 1857). Code that exists
and tests that pass are not proof here: a row is proven only by a real run of the owner's own Dot that used the feature
and succeeded, or by the owner trying it. A row with no such proof says **unproven**, with what is known.

**The limit is 30 days, checked every day, never in the gate.** `.github/workflows/maturity.yml` runs
`scripts/maturity_lapsed.py` each morning: a row whose date is more than 30 days old fails that job, so GitHub shows it,
and one issue titled "Maturity: proofs older than 30 days" lists the lapsed rows (opened the first time, its text
replaced while any lapse, closed once none does). The script exits 0 when no proof is stale, 1 when some are, and 2 when the table cannot be read: the workflow
fails on 2 without touching the issue, so a broken table is never taken for stale rows. The gate checks only the table's shape (`scripts/check_maturity.py`:
it reads, each feature once, each with how it was proven, its date a day and never after today): a date passes with the
calendar and no change of code, and a gate that failed on it would hold every merge until someone proved a feature
again. Why 30: in the 60 days to 2026-11-25 `main` took 1,739 commits, and in
the last 14 of them the code of the browser changed 44 times, the Claude plan's 29, the desktop's 15, the scheduler's
10 and the iMessage channel's 7. A proof older than a month is of a program that has since changed dozens of times
where it matters. A row that lapses is proven again (a real run, its date and how) or turned to **unproven** with the
date it was last proven: never moved forward without a new proof.

How the rows below were seeded (2026-11-25): from the owner's Dot store, read only, since its fresh start on
2026-10-30: the runs by source and by model and how they ended, the tools' successful calls on real runs, the
schedules' last firings, the meetings, the campaigns and the trading days; and from the records under `roadmap/`. No
content of a run is copied here, only counts and dates.

<!-- maturity:start -->
| Feature | Last proven | How | Known gaps |
| --- | --- | --- | --- |
| Chats and tasks from the web UI | 2026-11-25 | 95 of 118 runs from the web UI ended done since 2026-10-30 | |
| Schedules | 2026-11-25 | 83 of 92 firings' runs ended done; 12 schedules fired on 2026-11-24 or 25 | Several of the owner's schedules end failed on their own tasks |
| Helpers (delegate) | 2026-11-25 | 60 of 76 delegated runs ended done | |
| Teams of agents | 2026-11-24 | 16 of 16 team runs ended done | |
| The pre-push code review through Dot | 2026-11-25 | 282 of 315 review runs ended done | |
| Runs on the Claude plan (`claude_code`) | 2026-11-25 | 360 of 415 runs ended done | |
| Runs on OpenCode Go | 2026-11-25 | 181 of 210 runs ended done | |
| Runs on the Anthropic API | 2026-11-24 | 1 of 1 run ended done | One run is thin proof |
| Runs on Antigravity | 2026-11-22 | 2 of 3 runs ended done | |
| Shell and files | 2026-11-25 | Thousands of successful `shell`, `read_file`, `edit_file` and `write_file` calls | |
| Terminal sessions | 2026-11-24 | 98 successful `terminal_open` calls | |
| Dot's Chrome (browser tools) | 2026-11-25 | 973 successful `browser_act` calls, `browser_open`, `browser_eval`, `browser_upload` | |
| Logins from Dot's vault into the browser | 2026-11-25 | 19 successful `browser_fill_login` calls | |
| Web search, fetch and crawl | 2026-11-25 | 158 successful `web_search`, 286 `web_fetch`, 30 `web_crawl` calls | |
| Mail and calendar | 2026-11-25 | 29 successful `mail_search` and 12 `calendar_events` calls | |
| Memory | 2026-11-24 | 39 successful `memory_save` and 12 `memory_search` calls | |
| The Mac's desktop and AppleScript | 2026-11-23 | 7 successful `desktop_screenshot` and 13 `apple_script` calls | Few calls |
| Python with Dot's tools (`code_run`) | 2026-11-25 | 1 successful call | One call is thin proof |
| Videos (`watch_video`) | 2026-11-23 | 1 successful call | One call is thin proof |
| Notices to the owner over iMessage | 2026-11-25 | 20 notices sent, the newest on 2026-11-25 | Messages the owner sends to Dot over iMessage are not in this record |
| Campaigns | 2026-11-13 | One campaign ran to an achieved outcome | |
| Trading (paper) | 2026-11-21 | Paper trading days recorded daily to 2026-11-21 | Live trading is unproven |
| Morning brief | 2026-11-09 | Its schedule's last firing ended done | Switched off by the owner since |
| Evening check-in | 2026-11-08 | Its schedule's last firing ended done | Switched off by the owner since |
| The owner's iPhone (`phone_*` tools) | unproven | Only `phone_status` answered (2026-11-24); notices since say WebDriverAgent cannot be reached | No tap, type or screen of the phone on record |
| Watchers | unproven | None is set up in the owner's Dot since 2026-10-30 | |
| Dot's own initiative | unproven | Switched off in the owner's Dot | |
| Meetings (Google Meet) | unproven | Both tries, on 2026-11-12, failed before Dot was admitted | Meet never worked live |
| Meetings (Teams, Zoom) | unproven | Not built | |
| The iPhone app (DotNext) | unproven | Being rewritten; not yet passed its parity list on the real phone | |
| The iPhone's calendars, reminders and location (`phone_calendar_*`, `phone_reminder_add`, `phone_location`, item 2051) | unproven | New: tests only (a fake phone on a served daemon, DotEngine's contract tests) | Not yet tried on the owner's real iPhone |
| DotNext APNs and lock-screen Live Activity | unproven | Wire tests and a fake APNs transport | Verify registration, Turkish notices and Live Activity start, progress and end on the owner's signed iPhone |
| A schedule's or watcher's check (item 1855) | unproven | New: tests only | |
| Dollar estimates (item 1856) | unproven | New: tests only | |
<!-- maturity:end -->
