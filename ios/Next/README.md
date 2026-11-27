# DotNext: the rewrite of Dot for iPhone and iPad

The native app that replaces `ios/Dot` wave by wave (the plan lives in the owner's workspace,
`ios-rewrite-2026-11-24/PLAN.md`). It installs beside the old app: bundle id `dev.dotagent.phone.next`, app group
`group.dev.dotagent.phone.next`, its own Keychain access group, URL scheme `dotnext://`, display name "Dot Next". iOS 26
and later, iPhone and iPad. No push and no Live Activity until the swap (owner's decision): the old app keeps them.

Wave N0 gave the design system, the components and the gallery; wave N1-client the engine (`ios/DotEngine`, below)
and the screens wired to it: pairing, the chat list, a chat, the helpers and background sheets, settings. Wave N2 made
the chat whole (`App/ChatScreen.swift`, `App/ChatSheets.swift`, `Components/WorkLog.swift`, `AskDock.swift`,
`Answer.swift`, `Composer.swift`): the work log with the four tool modes (the table is on `ToolCallMode` in
`DotEngine/Sync/Transcript.swift`) and the fold of a finished run, markdown answers, the ask dock in the composer's place
(approvals, questions, hand-offs, secrets typed in a secure field), the composer (+ menu, model · effort chip, on-device
dictation, Send / Queue / Steer / Stop; while a run works and the draft has words a long press on the button offers
Steer now and Queue, the follow-up setting's way checked, and sends the draft the way picked; a run stops from the
composer's Stop, the chat's header has none), the queue sheet and the edit of a queued message, the run inspector, reactions.
While a run works, its step's thinking and words stream as the model writes them (the thinking in Show all and Hide
successful, as a row of the stretch of work with its first sentence; the words at a steady pace, the newest fading in);
in Hide all (the default) and Zen one live line under the last message says what the run does now (`WorkLineView`,
`workNow`: typing, thinking, the command it runs, the file it reads, waiting for you), and a tap on it shows the steps.
The floating capsule says Working with a clock that ticks every second whether events come or not. Dot's own tools
read as words with Dot's mark. A new chat opens as soon as its first message is sent, under the outbox's
key, and turns into the real chat in place once the Mac names it (item 1849). The transcript follows new words only
while the owner is at its end; their own scroll up stops it, a scroll back to the end starts it again, and it follows
once the words are laid out, so they are never left under the composer; a streamed word redraws the open chat alone
(the app's model publishes the store's snapshot by part, at most 15 times a second; items 1848 and 1926).

What a run costs the phone (item 1926): the daemon sends a model's words joined, about twelve frames a second; the app
draws at most 15 pictures a second; a running tool's shimmer draws 30 frames a second while its band moves and none while
it rests; in the background nothing streams (the supervisor ends every stream and the outbox's sending) and back in front
each stream opens again from its cursors, a chat catching up with one read of its view; Activity reads again when the
list's stream tells a run, an ask or a draft moved, not on a timer, and Connect apps reads every five seconds only while
a service waits on the Mac. A photo goes at most 1,600 px on its long side and a screenshot is decoded to that size
(`Data/ImageSizing.swift`). The app's own performance record (`Data/PerfReporter.swift`: MetricKit's daily measures and
diagnostics' counts, and the app's counters of each time it was open, `App/PerfCounters.swift`) goes to the paired Mac
alone (`POST /api/phone/metrics`, sent when the app opens) and Settings › Maintenance shows its last seven days. A step's thinking is
one folded line that a tap opens; the running work is one pill (plain words and clock | Stop | Plan 1/5 | queued |
helpers done/total), whose plan and people segments open the plan's checklist and the Agents sheet; the stream keeps
no plan card (item 1880), and a run's helpers are one group row whose tap opens a card of each (item 1957). The copy · reaction · time footer is on a run's final answer once it ended;
what the policy allowed with nobody asked is not drawn (the inspector keeps it).

Wave L1 (items 1800-1802): the chat list (`ChatListView`, `App/Live.swift`, self-contained so a bot's profile can show
it) selects several chats (Select in the bar or on a row's menu) and pins or deletes them together, with a Pinned
section on top. The composer's model chip, and a new chat before its first message, open the chat's settings
(`App/ChatSettingsSheet.swift`, rules in `ChatSettingsRules.swift`) on the app's one model catalog
(`Components/ModelCatalog.swift`): the recent models, the providers as folding groups with their marks, Default, ✓ and ☆
favourites, a search with a filter, the reasoning level on the model's own levels, fast mode, the approval mode, plan
first and hidden models; the picks go to the chat's choices or with a new chat's first message (`POST /api/runs`). The
microphone turns the composer into a recorder (`Components/VoiceRecorder.swift`, `Data/VoiceInput.swift`): ✕, a live
waveform, a clock, ✓; written down on the phone (Speech, on the device, a long dictation going on across the recognizer's
pauses) or on the Mac (`/api/voice/streams`), as Settings › Voice says (`Settings/VoicePage.swift`, with the preferred
order of the microphones, `Data/VoiceRules.swift`). A call, Siri, the app leaving the screen or a microphone going away
end a recording with what was heard.

Wave N3 made Settings whole (`Settings/`): the reference's structure with Dot's pages. This Mac (status, address, the
daemon's proof that it holds this phone's key, `GET /api/proof`, checked here and not yet a gate of the engine's reads; the diagnosis hop by hop,
iPhone network → Tailscale → the Mac → Dot → the key, with Copy diagnostics and a trace id; Tailscale on the Mac;
Forget this phone's key), Notifications (the three kinds of `/api/phone/push-settings`), General (Dot's and the owner's
names, the language, the clock, Queue or Steer, the confirmation of deleting a chat, dictation's language), Appearance
(light, dark or system, the text size with a live preview, Terminal and Code and diffs with a preview, a size of their
own and word break, the tool calls in chat, response streaming, the reading width), Chat (haptics, reactions'
follow-ups), Models (the main and fast models from the daemon's list, and the code review's model with its reasoning,
changed from the phone too: item 1770, a §24 picker sheet), Usage limits and caps (the plan's windows, today's tokens,
the caps of item 1731, lowered from the phone, raised on the Mac), Scheduled tasks (next or last run, paused, failed; a
switch on each row; edit, run now, delete; + makes a new one, item 1911, and a schedule's History lists its runs,
each opening its chat), Rules (the approval mode tasks start in and what asks in it; free mode,
switched off from the phone), Memory (the runs' proposed rewrites with Use and Drop, search, a new note, every note to read, edit, pin or
forget, and the Trash to restore from), Agents (`Agents/`: the list, an agent's page, a chat with it, its model), Campaigns (state, goal, last progress; a campaign's questions, notes and recent sessions; Stop, which asks, Resume and
Mark achieved as the daemon takes them), Health and version, Usage,
Maintenance (Dot's and the provider programs' versions, Continue after restart); what is set up at the computer stays on
the Mac (PLAN §26), which the list's last line says, but for Connect apps below (PLAN §27), and no Settings row opens
the web app; the phone does not engage the emergency stop (the Mac's failsafe does), and while it holds Settings says
so at the top with Release the stop. A search with synonyms finds any page in English or
Turkish. Every read and write says its failure the same way (`SettingsFailure`: unreachable with Diagnose, key refused
with Pair again, refused to a phone with the Mac's place, Dot too old). Pairing is a first-run screen: scan the code
Settings › Phone shows on the Mac (`dotnext://pair?host=…&code=…`, one tap pairs; the address code fills the address),
or enter the code; a pair link opened from elsewhere waits for the same tap; a failure says which hop failed.

Connect apps (`Settings/ConnectAppsSheet.swift`, items 1861 and 1862, PLAN §27) is a sheet over the Mac's Connectors
catalogue (`GET /api/plugins`, `/connections`, `/mcp/servers`, `/vault`, `/accounts`, read together): an "N connected"
pill that lists the connected services alone, the catalogue's sections as chips, Featured (the web's Popular, at most
eight), then each section; a row per service with its logo (the web's own files, `Resources/PluginLogos.xcassets`, made
by `scripts/sync_plugin_logos.py`, drawn in the theme's ink; else its glyph or its initial), its line and a Connect or
Connected pill; a floating search over the services and the skills. A row opens the service's detail: what each way
does and how it connects, who it is connected as, Disconnect with a confirmation for what Dot can take back (a
connected app added on the Mac or from another agent, 1Password's token, an official sign-in). Connect takes the
daemon's step: a Mac application's, a synced folder's and Dot's browser's sign-in (`POST /api/connections/{id}`) are
started from the phone and finished on the Mac, where macOS asks or the page opens; adding a connected app, an official
sign-in and 1Password's token are refused to a phone's key (item 678, `on_this_mac`), so their Connect says "Finish on
your Mac" on the row and names the Mac's page in the detail. A Disconnect of a sign-in the provider did not end shows
the daemon's notice of where to end it. While the daemon says a service waits, or a step started from the phone is not finished on the Mac yet, the sheet
reads again every five seconds, so "Finish on your Mac" turns into Connected with no pull to refresh; it stops with the
sheet.
No "For you" section: the daemon gives no suggestion for services.
The Activity tab (`Activity/`, item 1826) reads what Dot did on its own: "while you were away" with Mark as read for
every device, the drafts that wait (Send and Discard, each asked first), the proposals of Self-improvement (Use, Set
aside), what waits, works, is due, ended or was learned today (a row opens its chat), and the morning brief, the evening
check-in, Dot's own initiative and the watchers with their switches; their setup stays on the Mac (PLAN §26).
`scripts/e2e_activity.sh <simulator udid> <derived data> <shots dir>` runs `ActivityLiveUITests` against a throwaway
daemon seeded with `e2e_daemon.py --seed-activity`, exports its light and dark shots and reads on the daemon's side what
each tap wrote; it never sends a draft (a send goes through the Mac's own Mail or Messages).

## The engine: `ios/DotEngine`

A Swift package (iOS 26 and macOS 26; Foundation, Network, Security only: no UIKit, so the Mac app can use it):
`Contract/` (the daemon's sync contract, `docs/phone.md` "A chat kept in sync"), `Wire/` (HTTP, SSE parser, host,
retry policy), `Sync/` (the pure reducers of a chat and the list, frames, delivery states, the transcript filter, the
clock, the link), `Store/` (`SyncStore`, `Supervisor`, `Engine`), `Outbox/` (moved from the old app), `Cache/`
(`v2/` of the app group, kept with the daemon it came from: a reset Dot, another `store_id`, drops it and the outbox's doings
for the old chats, their words kept in the new chat's draft; `docs/phone.md` "Which daemon"), `Pairing/` (typed code,
Keychain). `swift test` in `ios/DotEngine` runs its tests on the Mac;
the DotNext scheme runs them on the simulator too. The tests never sleep: `ManualClock` is moved by hand, and
`FakeDaemon` (a `URLProtocol`) speaks the contract and misbehaves as a test says.

## Layout

- `Design/`: the tokens. `Palette` (four palettes: light, dark and their Increase Contrast steps; `ContrastAudit`
  measures every ink on every surface), `Tokens` (the palette as SwiftUI colours, `Tone.*`; `Space`, `Radius`, `Metric`),
  `Typography` (type roles on the system font with Dynamic Type, body 16/23; `ProseColumn`, the measured reading
  column), `Motion` (120/200/150 ms, Reduce Motion; haptics; the stream-tick gate), `Glass` (glass on floating chrome
  only, opaque under Reduce Transparency), `Symbols` (SF Symbols, one weight per context).
- `Components/`: rows and grouped sections, radio and switch rows (`InkSwitchStyle`), buttons, chips, the status word,
  empty and loading states, notices, the toast with Undo, sheets (inspector: ✕ only; edit: ✕ and ✓), the composer shell,
  the floating working capsule with the scroll-to-bottom circle, the transcript pieces (bubble, answer, code block, tool
  call row and group, helper group, speaker line), helper and background-work rows and their sheets.
- `App/`: the shell (the home of bots, PLAN §27: `Home.swift`, a bot's chat `BotChat.swift`, its profile
  `BotProfile.swift`, faces `BotFace.swift`), the gallery, sample content. `scripts/e2e_bots.sh` runs `BotsLiveUITests`
  on a throwaway daemon.
- `Resources/`: the asset catalog (app icon, accent = the ink) and `Localizable.xcstrings`.
- `Tests/`: `DotNextTests`. `scripts/`: the icon, the strings check, the screenshot harness.

## Decisions taken in N0

- **No hue.** The ink is the only accent (send, switches, links underlined, selection, the unread dot); amber only for
  what waits on the owner, red only for a failure, blue (`live`) only for a helper at work in the stream's helpers
  group (item 1957). No surface has a stroke.
- **The switch** is drawn (`InkSwitchStyle`): the system's white thumb disappears on a light ink track in the dark look.
- **iPad**: no tab bar (PLAN §27); a compact width is the home with a bot's chat pushed, a regular width a split view
  (the home as the sidebar, the chat as the detail) with the same selection, so collapsing keeps the open chat. Grouped pages are at most
  700 pt wide; prose in a chat is measured to 68 characters of the body font (`ProseColumn`, tested to stay in 60-75).
- **Density**: tool calls are 28 pt rows under a 44 pt group disclosure; a row is a full-width target 28 pt tall (an
  open question for the owner's review on the phone).

## Strings

Every user-visible string goes through `String(localized: "…", comment: "…")` and lives in
`Resources/Localizable.xcstrings` with a Turkish translation (the owner reads Turkish). `StringCatalogTests` fails on a
key without Turkish, without a comment, or stale; `scripts/check_strings.sh <derived data>` fails on a string of the code
that the catalog lacks and on raw literals in SwiftUI calls. To add strings: build, run the check, copy the synced
catalog it names over `Resources/Localizable.xcstrings`, add the Turkish to `scripts/translations_tr.py`, run
`python3 ios/Next/scripts/apply_translations.py`.

## Checks

```bash
cd ios && xcodegen generate
xcodebuild test -project Dot.xcodeproj -scheme DotNext -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -configuration Debug CODE_SIGNING_ALLOWED=NO -derivedDataPath /tmp/dotnext-dd
Next/scripts/check_strings.sh /tmp/dotnext-dd
(cd DotEngine && swift test)
```

## CI

`.github/workflows/ios.yml` runs the above on GitHub's macOS runners, so the Mac does not have to: `engine`
(`swift test`), `build` (XcodeGen, one `build-for-testing`, the String Catalog check), `tests (chat)`,
`tests (controls)` and `tests (rows)` (the unit and UI tests from that build; each test of `SettingsControlsUITests`
takes 16-27 minutes on the runner, so each has a part of its own), `e2e` (`scripts/e2e_settings.sh` against a
throwaway daemon of the same commit), and `ios`, which passes only when all of them did.

- A pull request that changes `ios/` or this workflow runs `build` alone (about ten minutes: the app and its tests compile, the String
  Catalog is checked; item 1886 left pull requests no macOS job, and a compile error reached `main`). The push to
  `main` that merges one, when it changes `ios/**`, runs `engine`, `build` and `tests (chat)` (about 20 minutes); a
  failure there opens the issue "main is red on macOS" (`scripts/main_macos_green.py`).
- The whole run (`tests (controls)`, `tests (rows)` and `e2e` too, about an hour) runs every night and when asked:
  `gh workflow run ios.yml --ref <branch>` runs it on a branch before its pull request merges.
- A run holds two macOS runners at most, so that three of the account's five stay free for the gate.
- A test that fails, unit or UI, runs once more (`-retry-tests-on-failure`; `e2e` runs its whole script once more): a
  fresh runner's simulator sometimes fails to launch the app or start the test runner. A test that fails twice fails
  the part; one that passed only on its retry is named in the job's summary as "passed on retry: <test>" and as a
  warning, so the retry hides no flaky test.

Run a test on the Mac only to reproduce a failure CI showed, and the screenshots for the owner.

```bash
gh pr checks <pr>                                   # the ios jobs among the others
gh run list --workflow ios.yml --branch <branch>    # the runs, newest first
gh run view <run id> --log-failed                   # the lines of the step that failed
gh run download <run id> -n ios-tests-chat -D /tmp/ios-chat   # also ios-tests-controls, ios-tests-rows, ios-e2e
open /tmp/ios-chat/tests.xcresult                   # the whole result in Xcode; shots/ holds every screenshot as PNG
```

The artifacts are kept 7 days: `tests.xcresult` (or `e2e.xcresult`, with `e2e-logs/`: the daemon's and xcodebuild's
logs of each try, and the first try's result when it was retried) and `shots/`, its attachments exported with
`xcrun xcresulttool export attachments` (`shots/manifest.json` names the test of each file): the screenshot and
description XCTest keeps of a failure, and whatever a test attaches with `XCTAttachment` and `lifetime = .keepAlways`.
A passing test keeps no screenshot of its own; the owner's screenshots of every scene stay `scripts/shots.sh` on the
Mac.

## End to end against a throwaway daemon

```bash
t=$(mktemp -d)
DOT_HOME=$t/home DOT_LOG_DIR=$t/logs DOT_CHROME_DIR=$t/chrome uv run python ios/Next/scripts/e2e_daemon.py 18765
curl -X POST -H "Authorization: Bearer dotnext-e2e-token" http://127.0.0.1:18765/api/phone/pairing   # the code
```

Build DotNext for the simulator signed locally (`CODE_SIGN_IDENTITY=-`, so it has its app group), pair with
`http://127.0.0.1:18765` (debug builds only take a loopback address) and the code. The script's model replies by the
message: `helper: …` starts a helper, `wait: …` waits in `wait_until`, `slow: …` takes 8 s. A debug launch with
`-DotNextScene live-chat -DotNextProbe 20` sends 20 messages in the first chat and writes send → first event on screen
to `Library/Caches/dotnext-latency.txt`. `ios/Next/scripts/e2e_streaming.sh <udid> <derived data> [test] [video]`
runs `StreamingLiveUITests` on such a daemon: a new chat stays open when its first message is sent, and the
`stream:` script (four steps that think aloud and read a file, then a long answer, about 40 words a second) is measured
by the debug frame meter (`-DotNextFrameMeter 1`: frames, hitches, frames over 16.7 ms, hitch time per second, one line
per run in `Library/Caches/dotnext-frames.txt`), and by the phase meter (`App/PerfCounters.swift`): one line per stretch
of the chat open, the home and the background (`StreamingLiveUITests.testAStreamingRunCostsLittleUnseen`) with the
process's CPU milliseconds and wakeups a second (the kernel's `TASK_POWER_INFO`), the pictures published and the reveal's
steps a second and the bytes read and sent a minute, in `Library/Caches/dotnext-phases.txt`.

## A chat's message actions and the inspector, end to end

`scripts/e2e_chat_actions.sh <udid> <derived data> <shots folder>` (inside the Xcode lock) makes six chats on a
throwaway daemon with the script words `write:` (a file the inspector can undo), `draft:` (a draft card), `bg:` (a
command in the background), `fail:`, `helper:` and a plain one, then `ChatActionsLiveUITests` walks them in the dark
look (shots only) and the light look (Edit from here, Retry and Continue, the draft's edit and Discard, Undo, Write a
recap, Stop, a helper's chat); the script reads on the daemon's side what each tap did. `e2e_daemon.py --model
claude_code:haiku` runs the same daemon on a real model.

## The chat's fixtures

Debug builds draw the real chat screen from a fixture with no daemon (`App/Fixtures.swift`, decoded from the contract's
JSON): `-DotNextScene n2-chat`, `n2-asks`, `n2-secret`, `n2-handoff`, `n2-queue`, `n2-editing`, `n2-failed`,
`n2-offline`, `n2-deleted`, `n2-agents`, `n2-inspector`, `n2-model` (the chat's settings). The UI tests (`UITests/ChatUITests.swift`, target
`DotNextUITests`) run on them. The e2e daemon's script words for the chat: `approve:`, `question:`, `secret:`, `tools:`,
`fail:`, `stream:`, besides `helper:`, `wait:` and `slow:` (the daemon's home asks in the supervised mode).

## Settings' fixtures and end to end

Debug builds draw Settings on a daemon served from memory through a `URLProtocol` (`Settings/SettingsFixture.swift`,
the routes' own JSON): `-DotNextScene settings-home`, `settings-search`, `settings-mac`, `settings-notifications`,
`settings-general`, `settings-appearance`, `settings-chat`, `settings-voice`, `settings-models`, `settings-caps`, `settings-caps-refused`,
`settings-schedules`, `settings-schedule-edit`, `settings-rules`, `settings-memory`, `settings-system`, `settings-campaigns`, `settings-campaign`,
`settings-stopped`, `settings-offline`, `settings-key-refused`, `settings-old-daemon`, `settings-web`,
`settings-connect-apps`, `settings-connect-apps-detail`, `settings-connect-apps-offline`, `pairing`, `pairing-link`. `UITests/SettingsUITests.swift` runs on them, and `SettingsControlsUITests.swift` taps every row of
Settings and every control of each native page and fails on one after which nothing changed.
`scripts/e2e_settings.sh <simulator udid> <derived data> [daemon checkout]` starts a throwaway daemon with a seeded
proposal, pairs a phone, makes a schedule, runs `SettingsLiveUITests` (every page that writes) and reads on the
daemon's side what each tap wrote; `ConnectAppsLiveUITests` reads Connect apps (a chip, the search, a detail).

`scripts/e2e_chats.sh <simulator udid> <derived data> [model ref]` starts a throwaway daemon on a free port, makes two
"[test]" chats and runs `ChatListLiveUITests` (a UI test that fails fails it): two chats pinned and deleted together
(the deletion read on the daemon), and a new chat on the model, level
and approvals picked in its settings (a real model, `claude_code:haiku` by default: the owner's Claude plan through the
`claude` CLI), whose run record it reads, and the recorder on the Mac's dictation.

## Screenshots

`scripts/shots.sh <DotNext.app> <out dir> "tr en" <booted simulator udid>...` captures every scene (the gallery pages,
the home, the chat and its sheets) in light and dark, at Large and AX3. The debug build opens a scene with
`-DotNextScene <name>` (`LaunchScene.names`) and writes `Library/Caches/dotnext-ready.txt` once it is drawn; the script
waits for that file before each capture.

## The icon

`swift ios/Next/scripts/make_icon.swift ios/Next/Resources/Assets.xcassets/AppIcon.appiconset`: a flat graphite tile
with a light disc and two dark eyes (owner's decision D12), in the default, dark and tinted looks.
