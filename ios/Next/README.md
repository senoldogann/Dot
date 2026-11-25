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
dictation, Send / Queue / Steer / Stop), the queue sheet and the edit of a queued message, the run inspector, reactions.
While a run works, its step's thinking and words stream as the model writes them (the thinking in every mode but Zen),
and the floating capsule says what the run does now (thinking, writing, the command that runs) with a clock that ticks
every second whether events come or not. The copy · reaction · time footer is on a run's final answer once it ended;
what the policy allowed with nobody asked is not drawn (the inspector keeps it).

Wave N3 made Settings whole (`Settings/`): the reference's structure with Dot's pages. This Mac (status, address, the
daemon's proof that it holds this phone's key, `GET /api/proof`, checked here and not yet a gate of the engine's reads; the diagnosis hop by hop,
iPhone network → Tailscale → the Mac → Dot → the key, with Copy diagnostics and a trace id; Tailscale on the Mac;
Forget this phone's key), Notifications (the three kinds of `/api/phone/push-settings`), General (Dot's and the owner's
names, the language, the clock, Queue or Steer, confirmations, dictation's language), Appearance (light, dark or
system, the text size with a live preview, the tool calls in chat, response streaming, the reading width), Chat
(haptics, reactions' follow-ups), Models (main and the roles from the daemon's list; the code review's models read
only), Usage limits and caps (the plan's windows, today's tokens, the caps of item 1731, lowered from the phone, raised
on the Mac), Scheduled tasks (next or last run, paused, failed; pause, resume, edit), Rules (read), Memory (search, the
runs' proposed rewrites with Use and Drop), Health and version, Usage, Maintenance (Dot's and the provider programs'
versions, Continue after restart); what is set up at the computer stays on the Mac (PLAN §26), which the list's last
line says, and no Settings row opens the web app. A search with synonyms finds any page in English or
Turkish. Every read and write says its failure the same way (`SettingsFailure`: unreachable with Diagnose, key refused
with Pair again, refused to a phone with the Mac's place, Dot too old). Pairing is a first-run screen: scan the code
Settings › Phone shows on the Mac (`dotnext://pair?host=…&code=…`, one tap pairs; the address code fills the address),
or enter the code; a pair link opened from elsewhere waits for the same tap; a failure says which hop failed.

## The engine: `ios/DotEngine`

A Swift package (iOS 26 and macOS 26; Foundation, Network, Security only: no UIKit, so the Mac app can use it):
`Contract/` (the daemon's sync contract, `docs/phone.md` "A chat kept in sync"), `Wire/` (HTTP, SSE parser, host,
retry policy), `Sync/` (the pure reducers of a chat and the list, frames, delivery states, the transcript filter, the
clock, the link), `Store/` (`SyncStore`, `Supervisor`, `Engine`), `Outbox/` (moved from the old app), `Cache/`
(`v2/` of the app group), `Pairing/` (typed code, Keychain). `swift test` in `ios/DotEngine` runs its tests on the Mac;
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
- `App/`: the shell (four tabs), the placeholder screens, the gallery, sample content.
- `Resources/`: the asset catalog (app icon, accent = the ink) and `Localizable.xcstrings`.
- `Tests/`: `DotNextTests`. `scripts/`: the icon, the strings check, the screenshot harness.

## Decisions taken in N0

- **No hue.** The ink is the only accent (send, switches, tab tint, links underlined, selection); amber only for what
  waits on the owner (also the Chats badge), red only for a failure. No surface has a stroke.
- **The switch** is drawn (`InkSwitchStyle`): the system's white thumb disappears on a light ink track in the dark look.
- **iPad**: the tab bar stays the top level on every width; inside Chats and Agents a compact width is a stack and a
  regular width a split view with the same selection, so collapsing keeps the open item. Grouped pages are at most
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
to `Library/Caches/dotnext-latency.txt`.

## The chat's fixtures

Debug builds draw the real chat screen from a fixture with no daemon (`App/Fixtures.swift`, decoded from the contract's
JSON): `-DotNextScene n2-chat`, `n2-asks`, `n2-secret`, `n2-handoff`, `n2-queue`, `n2-editing`, `n2-failed`,
`n2-offline`, `n2-deleted`, `n2-agents`, `n2-inspector`. The UI tests (`UITests/ChatUITests.swift`, target
`DotNextUITests`) run on them. The e2e daemon's script words for the chat: `approve:`, `question:`, `secret:`, `tools:`,
`fail:`, besides `helper:`, `wait:` and `slow:` (the daemon's home asks in the supervised mode).

## Settings' fixtures and end to end

Debug builds draw Settings on a daemon served from memory through a `URLProtocol` (`Settings/SettingsFixture.swift`,
the routes' own JSON): `-DotNextScene settings-home`, `settings-search`, `settings-mac`, `settings-notifications`,
`settings-general`, `settings-appearance`, `settings-chat`, `settings-models`, `settings-caps`, `settings-caps-refused`,
`settings-schedules`, `settings-schedule-edit`, `settings-rules`, `settings-memory`, `settings-system`,
`settings-stopped`, `settings-offline`, `settings-key-refused`, `settings-old-daemon`, `settings-web`, `pairing`,
`pairing-link`. `UITests/SettingsUITests.swift` runs on them, and `SettingsControlsUITests.swift` taps every row of
Settings and every control of each native page and fails on one after which nothing changed.
`scripts/e2e_settings.sh <simulator udid> <derived data> [daemon checkout]` starts a throwaway daemon with a seeded
proposal, pairs a phone, makes a schedule, runs `SettingsLiveUITests` (every page that writes) and reads on the
daemon's side what each tap wrote.

## Screenshots

`scripts/shots.sh <DotNext.app> <out dir> "tr en" <booted simulator udid>...` captures every scene (the gallery pages,
the tabs, the chat and its sheets) in light and dark, at Large and AX3. The debug build opens a scene with
`-DotNextScene <name>` (`LaunchScene.names`) and writes `Library/Caches/dotnext-ready.txt` once it is drawn; the script
waits for that file before each capture.

## The icon

`swift ios/Next/scripts/make_icon.swift ios/Next/Resources/Assets.xcassets/AppIcon.appiconset`: a flat graphite tile
with a light disc and two dark eyes (owner's decision D12), in the default, dark and tinted looks.
