# Dot for iPhone

A native SwiftUI app for Dot (items 997, 1353 and 1361): the chat list with the agents strip, chats that stream, the
approvals and questions a run asks, the helpers it waits for, the queue, the schedules and loops, and the models, all
over the daemon's own API; plus what a web page cannot do on iOS: remote push and the phone's one Live Activity of the
owner's runs on the lock screen and in the Dynamic Island. The web UI stays as it is: the app opens it full screen from
Settings › Open web UI (and to pair in the page), but it is no longer the app's main screen.

- `Dot/`: the app (`dev.dotagent.phone`).
  - `Core/`: pure rules and records, no UI: address parsing, navigation policy and bridge validation of the web
    fallback; the daemon's records (`Models`, `JSONValue`); `ChatState`, the reducer that turns runs and events into
    rows; `QueueOrder`; `MessageBlocks`.
    Resilience rules live here too (item 1371): `RetryPolicy` (tries, waits, jitter, what is worth repeating),
    `OutboxItem` (what waits for the Mac), `ConnectionBanner` (what the one line says) and `PrefetchRules`.
    `ActivityLease` (item 1440) is the rule of the activity lease: when the app tells Dot it is in front of the owner;
    `PushSettings` (item 1441) the three push switches and what a flip does; `NeedsYou` (item 1442) the inbox's list, its rows
    and what may be answered from them; `Away` (item 1443) what Dot did on its own and the card that says it; `Handoff` (item 1444) the card of a step only the owner can do.
  - `Data/`: `APIClient` (URLSession over `DaemonHost`, the phone key from `PhoneKeyStore`; a read is tried again by
    `Retrier` as its `RetryPolicy` says), `SSEParser` and `EventFeed` (resume from the last event id, backoff, wait for the
    link, reconnect on foreground and on a change of the network), `ChatCache` (JSON files per host: versioned, whole or
    absent, within a size), `ChatSession` (one open chat, an actor), `NativeStore` (the list, the lifecycle stream, the
    link, what is read ahead, background and foreground), `LinkMonitor` (the phone's link, Low Data Mode and cost),
    `Outbox` and `OutboxModel` (what the owner did with no link, kept on disk and sent once in order), `Retrier` and
    `Coalescer`, `BackgroundGrace`.
  - `Features/`: the screens: `ChatListView`, `ChatView` with `Composer`, `Dock` (the capsule over the composer and the queue's sheet; `Core/DockSummary` says what it holds),
    `RunInspector`, `SettingsViews` (models, schedules), `PairView`, `NewChatView`, `ModelPicker`, and the design system
    (`Theme`, `Chrome`, `ToolGlyph`, `DesignGallery` in debug builds); `Shared/DotPalette.swift` is its colour tokens.
  - `Adapters/` the Keychain, UserDefaults and the push/share HTTP client; `Push/` the token registration;
    `Views/` the address screen and the web UI fallback (`DotWebView`).
- `DotTests/`: the unit tests (XCTest) of the reducer, the SSE parser and feed, the cache, the queue order, the link
  style, the requests, and (slice 2) the model and level rules, older runs, a step's detail, a message's actions, drafts,
  dictation's rules and the links.
- `DotTests/` also holds the harness of item 1371: `FakeDaemon` (a daemon in the test process behind a `URLProtocol`:
  the routes the app reads and sends, a key kept for a message as `send_once` keeps it, a response that is lost after
  the daemon took the request, latency, a share of requests lost, no link at all; whether a request is lost depends on
  its path and how often that path was asked, so a run is the same twice) and `PerfTests` (time to the first rows and to
  the rows being the Mac's, in the scenarios of the table below).
- `DotUITests/`: the same screens driven by taps in the simulator (XCUITest, scheme `DotUI`) against a scratch daemon
  with a scripted model; they skip when `/tmp/dot-ui-daemon.json` is missing and write screenshots to `/tmp/dot-ui-shots/`.
  `GlassShotsTests` (item 1380) takes the design's screenshots of the screens that need a tap, from
  `/tmp/dot-glass-daemon.json`, to `/tmp/dot-ui-shots/glass/`.
- `DotLiveActivity/`: the widget extension (`dev.dotagent.phone.live`) that draws the Live Activity.
- `DotShare/`: the share extension (`dev.dotagent.phone.share`, item 1009): what another app shares goes to Dot as a
  new chat. It reads the host from the app group's defaults and the key from the shared Keychain access group.
- `Shared/DotFleetAttributes.swift`: the activity's attributes, compiled into both targets.
- `project.yml`: the XcodeGen spec. `Dot.xcodeproj` is generated from it and is not committed.

## What the native app does, and what it leaves to the web UI

Native: pairing with the code of Settings › Phone, the chat list (working, waiting on you, unread; pull to refresh; drawn
from disk at launch), a chat (messages that stream in, a tool step as one line, approval and question cards with Allow
and Deny, and over the composer ONE capsule of glass (item 1390) whose segments are the
run's status (working with its clock, waiting on you, waiting on N helpers), the helpers that work on in the background,
the chat's loops ("every 10 min") and the queue ("3 queued"), divided by hairlines, each opening its own sheet and none
stacked; a segment exists only while it has something to say, so a chat at rest has no capsule; the queue's sheet gives
each message an up and a down arrow, Send now (to the run at work) and Remove, and Resume when a stop held the queue;
the round button to the newest message stands beside the capsule; Send queues while a run works, and Stop with an empty
box; a photo to attach), the
run inspector (the run, its plan, its helpers, the chat's schedules and loops with their switches), the Schedules list and
the models (the default and the review model) in Settings, notifications and the Live Activity. Slice 2 (item 1361):

- **New chat**: the pencil of the list opens a sheet whose message starts a chat of its own (`POST /api/runs`, `mode`
  `auto`, `thread_id` null) on the model and reasoning level picked in its pill (none picked: the daemon's own); a model
  the daemon refuses (422 `ModelUnavailableError`, item 1350) is named with its words and a button sends the same words on
  the default model. An agent of the strip that has no chat opens the same sheet for it (`agent_id`): its first message
  starts its chat. A message from the sheet is sent again with the same key when its answer was lost.
- **Model and level of a chat**: the inspector's **Model for this chat** (reasoning first, as it is the quick change, then
  the models that use tools by provider) keeps the choice with the chat (`PATCH /api/threads/{id}` with all six `choices`
  keys, nulls included) and every message of the chat carries the chat's choices and its agent, as the web composer's
  messages do.
- **Older messages**: a chat holds its newest twelve runs; reaching the top (or the line's tap) reads twenty more
  (`GET /api/runs?thread_id&limit=20&before=<created_at of the oldest held, every digit>`), put above the first row with
  the transcript scrolled back to it (`scrollTo(anchor)`; not measured by a test); the line goes when a page comes short. Older runs come from their records (the
  message and the answer).
- **A step's detail**: a tap on a step's line opens its arguments and result, read from the step's two events after its
  call (`GET /api/runs/{id}/events?after=`), each clipped (2,000 characters an argument, 6,000 all, 8,000 the result) at a
  character boundary, with the number of characters left out said.
- **A message's actions** (a hold): Copy, Select text, the seven reactions on what Dot said
  (`POST/DELETE /api/runs/{id}/messages/{answer|step-n}/reactions`; the ones that stand are small signs under it), Edit
  from here on your own first message of a run (`POST /api/runs/{id}/rewind` with the files kept; asks first; not while a
  run works). **Drafts**: what is typed and not sent is kept per chat (and per new chat) in `drafts.json` beside the chat
  cache, written a moment after the last key, forgotten with the phone's key.
- **The microphone** (`Dictation`): on a press it asks for the speech and the microphone permissions (not at launch) and
  listens with Apple's Speech framework, **on the phone only** (`requiresOnDeviceRecognition`; a language with no
  on-device recognizer says so and does not send the audio away), putting the words heard after what was typed. It ends in
  a line over the box, never a silent button: a permission off (with a way to Settings), no recognizer now, no microphone.
  Info.plist has `NSSpeechRecognitionUsageDescription` and `NSMicrophoneUsageDescription`.
- **Opening a chat from outside**: the Live Activity's card opens the run that waits for you (else one at work, else the
  first) and each row its own run, as `dotagent://run/<run id>` (`CFBundleURLTypes`; the link is parsed by
  `DotDeepLink`, a run id that is not plainly an id opens nothing); a notification's tap and its **Open** action (new next
  to Approve and Deny; it comes to the front) open the chat of the alert's run, reading the run's own page and drawing
  the chat from it when the list does not hold it yet (a cold launch from the tap).

Awareness (items 1440 to 1446: the app knows when Dot needs the owner, stays quiet while they look at it, and says what Dot
did while they were away; the daemon's side is `docs/phone.md`, "The iPhone app's calls"):

- **The activity lease** (item 1440): the app tells Dot it is in front of the owner (`POST /api/phone/activity`:
  `foreground` and the chat on the screen) as its scene becomes active, every 25 s while it stays so and at once when the
  chat on the screen changes, and `background` at once when the scene goes inactive or to the background, so that Dot's
  alerts of a task's end stay quiet while the owner looks and go the moment they do not. The rules are `Core/ActivityLease`
  (`ActivityLeaseRules`: no clock, no network; `ActivityLease`: the clock, the timer and the sender that a test replaces);
  a report is one try that does not wait for a link, one on its way at a time with the newest behind it, and one that
  does not get through is only logged (the lease ends by itself in 45 s: the safe side). `NativeStore` owns the lease (a
  store exists for a paired phone only, so an unpaired app says nothing), knows the chat on the screen from the sessions
  it holds (`chatOnScreen`) and is told the scene's phase by `AppModel.sceneChanged`.
- **Push switches** (item 1441): Settings has a **Notifications** group of three switches (`Core/PushSettings`: Needs you,
  Ready, Done, each with its one line), read with `GET /api/phone/push-settings` when Settings opens and when the link is
  back, written as all three with `PUT` on a flip. `PushSwitches` is the rule: a flip shows at once, one write is on its
  way at a time (a second flip goes after it with the latest of all three), a write that is not kept puts the switches
  back to what the daemon held with a plain line (`words(forStatus:)`), and without the daemon's answer the last switches
  the phone knew are shown dimmed and do not move. `AwarenessModel` (one per store) draws them from `awareness.json` in the
  cache folder before the network answers. The old row that opens the iPhone's own page of this app's settings is now
  "iPhone notification settings".
- **Needs-you inbox** (item 1442): `GET /api/needs-you` is read on start, on foreground, on a pull to refresh, when an alert
  arrives or a push wakes the app, and on the stream's notices that change it (`NeedsYouRules.refreshes`); the last read is
  kept in `awareness.json` and drawn at once, and the app icon's number follows `count` after every read
  (`AwarenessModel.systemBadge`). The chat list has one card at the top while anything waits (`NeedsYouEntry`) that opens
  `NeedsYouView`: one row each (`Core/NeedsYou`), newest first, who asks and in which chat. An approval has Allow once and
  Deny (the line taken apart by `ApprovalPresentation`; no Allow once when the line is as long as the daemon cuts a title,
  which it does without a mark), a question its choices as buttons with the recommended one marked or a field for words,
  both through the outbox like the chat's card; a secret has no way to answer (its row says "Dot needs a secret: enter it
  on your Mac"); a hand-off is answered in the chat. A row opens its chat.
- **"While you were away"** (item 1443): when Dot did something on its own since the owner last looked (what a schedule, an
  agent's standing chat or a background run did), one flat card at the top of the chat list (`AwayCardView`, the pure rules
  in `Core/Away`) says how many things and how they ended, lists the three newest (each opens its chat) and how many more,
  and how many approvals Dot answered for the owner. It is read against the mark Dot keeps for every device
  (`GET /api/away/read`; the first visit marks the moment and shows nothing) and put away with `POST /api/away/read` (the
  card goes at once; a mark that does not get through is kept in `awareness.json` and sent first by the next read).
- **Hand-off card** (item 1444): a chat whose run waits for a step only the owner can do in the browser (a `handoff` row of
  the needs-you list) shows one calm card (`HandoffCardView`, the rules in `Core/Handoff`): "Dot is waiting for you in the
  browser on <host>" and **Open in Safari**, which opens the run's page of the web UI on the address the app holds
  (`https://<mac>/#/runs/<id>`) in the system's browser; the chat is still `dotagent://run/<id>`. The card carries only the host
  the daemon's fixed line names (and only when it is plainly a host), never a credential or page text; the inbox's row has
  the same words and button.

In the web UI: everything else (agents and teams, memory, rules, settings, the vault, the cards of a secret); the plan is in `roadmap/1353.md` and `roadmap/1361.md`.

How it stays fast: a chat read before opens from memory in its first frame, otherwise from `ChatCache` (the newest runs
and the last 400 stored events), then asks only for the events after the last id it holds; reading, reducing and writing
happen in `ChatSession`, off the main actor, which hands the screen a new `ChatState` at most every 40 ms while words
stream. The run's stream is followed with `after=<id>` and `Last-Event-ID: <id>` (item 1352), so a reconnect reads no
history again; it comes back at once when the app comes to the front or the network changes, and otherwise after 0.5 s,
1, 2, 4, 8 and 15 s. The transcript follows new words only while the reader is at the bottom.

## On a bad link (items 1370, 1371 and 1391)

A socket that is dead and does not say so (item 1391): a phone back on a path that died while it slept, or one whose Wi-Fi gave
way to the cellular link, holds a connection the system believes in for 45 s. The feed (`EventFeed`) does not wait for that:
a connection that has answered nothing within 8 s (`connectTimeout`) and an open stream that says nothing for 35 s
(`idleTimeout`; the daemon writes a keepalive every 15 s, so two missed and a little more) are dropped and the feed goes on,
from the last event it read, on a new one (`SilentSocketTests`: the silence, the connection that never answers, the keepalives
that keep a stream, the kick that still reconnects at once). Other phone apps do the same with a probe; Dot needs no probe
because its stream writes its own heartbeat.

What was measured, and what each part does now. Numbers are from the iPhone 17 Pro simulator against `FakeDaemon` with
the profile 2 to 8 s of latency per request and three requests in ten lost (`TEST_RUNNER_DOT_PERF_SCALE=1`); the simulator
shares the Mac's CPU and disk, so only the shape carries to a phone, and no real cellular network was tested.

| Moment | Before | After |
| --- | --- | --- |
| List on the screen at launch (disk) | 0.6 ms | 1.0 ms |
| Chat on the screen, cold launch or relaunch, any network | 10 to 15 ms | 14 to 20 ms |
| Chat is the Mac's, relaunch, good network | 24 ms | 22 ms |
| Chat is the Mac's, relaunch, worst network | not in 60 s | 8.0 s |
| Chat is the Mac's, foreground after the streams died, worst network | not in 60 s | 8.0 s |
| A message typed with no link | lost to "Not sent" until Retry | kept, goes by itself once the link is back |

- **The disk cache** (`ChatCache`): `list.json` (list and agents), `extras.json` (models, providers, schedules), one
  `chat-<id>.json` each (runs, helpers, the last 400 events, the queue, schedules and reactions, the list time it was read
  up to), `drafts.json` and `outbox.json`. Each is written whole through a temporary file renamed over the old one, and
  not again when its bytes did not change; a file that does not read, or is of a newer layout, is dropped and read from
  Dot again (`schema` 2 reads layout 1 too). The folder is kept within 24 MB: past it the chats read or written longest
  ago go first (a read touches the file). The list is decoded at launch (about a millisecond for a hundred chats) and
  the first eight chats are decoded off the main actor while the screen is made.
- **Reading**: `ChatSession` asks together for the chat's list of runs, its helpers, the questions that wait, its newest
  run's page and the events after the cursors of the runs it holds, and draws the events as they arrive: one round trip
  on a bad link where it was four in a row. A cursor moves only when the events are in the state, so a read cut short
  loses none. Past 500 events of a run, the newest 400 are read instead (`?tail=`) and a line says that steps are not
  shown. A read that failed says "reconnecting", keeps the rows and tries again by itself (0.5 s doubling to 15 s, with
  jitter), or waits for the link when there is none. A state the screen already has is not handed over again.
- **Requests** (`APIClient`): reads time out after 10 s, writes after 30 s, a session waits for connectivity up to 45 s
  (photos 10 minutes), reads are tried three times, a refresh of the list is one request however often it is asked, and
  leaving a chat cancels its reads. Both the lifecycle stream and a run's stream wait for the link instead of trying.
- **The outbox**: see the next section.
- **The line** (`ConnectionBannerView`, wired by one stack in `RootView`): *Offline — showing saved chats · last updated 3
  min ago*, *Offline — 2 waiting to send*, *Reconnecting…* (only when it lasts 1.5 s), or the key refused; nothing when
  all is well, and no clock runs while it is not shown.
- **Background**: after 20 s out of sight the streams are closed and nothing runs; back in front the stream, the open
  chats and the list are asked at once. `BGAppRefreshTask` (`dev.dotagent.phone.refresh`, mode `fetch`; no entitlement)
  and a push that says `content-available` read the list and the chats that moved on within 20 s.
- **Read ahead**: after a list read, the chats the list says moved on past what is held, six at most (three when the
  link is expensive, none when Low Data Mode is on), one after another, never the chat that is open.

### The outbox

A message, an answer to an approval or a question, a stop, a removal from the queue, a new order of it, and a message's
photos are written to `outbox.json` (the photos beside it) before anything is asked of the network, and sent in order by
one loop. A message goes with its own key (`POST /api/threads/{id}/queue`, `key`): the daemon keeps a key for a day and
answers a repeat with what the first made (`server/sending.py`; 409 while the first is under way, which the loop waits
out), so an answer lost after the daemon took the message, an app killed while it was sent, and a link that came back
twice send it once. A message that waited more than a day after a first try is left as "Not sent" for the owner, and
goes under a new key when asked. An answer that finds its card closed (409 or 404) reads the card and says how it stands
(*That question had expired when your answer reached Dot, so it was not used.*); a stop or a removal that finds nothing
to do is done; a change the daemon refuses is told and dropped; a message the daemon refuses stays as "Not sent" with
its words and does not hold the next one. A failure that may pass (no link, a daemon that is down or busy) keeps the order:
the message waits and the later ones behind it.

### Running the checks

- `cd ios && xcodegen generate && xcodebuild test -project Dot.xcodeproj -scheme Dot -destination 'id=<simulator>' -configuration Debug CODE_SIGNING_ALLOWED=NO`
  runs everything of `DotTests`, `PerfTests` at a twentieth of the profile's waits. `TEST_RUNNER_DOT_PERF_SCALE=1` runs the
  real profile (about 80 s more) and `TEST_RUNNER_DOT_PERF_REPORT=/tmp/perf.txt` writes the lines `PERF <scenario> <metric> <s>`.
- The simulator run: start the scratch daemon (`/tmp/dot-ui-res-daemon.json`, port 18741), then
  `python3 ios/scripts/flaky_proxy.py 18742 18741 /tmp/dot-ui-res-mode` (modes `pass`, `bad[:scale]`, `slow`, `down` in
  that file, read for each request), write `/tmp/dot-ui-res-proxy.json`
  (`{"origin": "http://127.0.0.1:18742", "mode_file": "/tmp/dot-ui-res-mode", "link_file": "/tmp/dot-ui-res-link"}`) and run
  `xcodebuild test -scheme DotUI -only-testing:DotUITests/DotUIResilienceTests`. The debug build reads the phone's link from
  `-DotLinkFile <file>` (`up` or `down`), as the simulator cannot be taken off the Mac's network.

## The design (items 1380 to 1382)

The look is one system, in `Features/Theme.swift`, `Features/Chrome.swift` and `Shared/DotPalette.swift`:

- **Colour** comes from `DotPalette` in two looks that follow the system's appearance: dark (near-black `#0A0A0B`,
  surfaces `#121214` and `#1B1B1F`, hairlines at 9 % white) and light (a cool white), one calm blue (ink `#6AA4FF` in the
  dark, `#2559C9` in the light; a fill with a very slight top-to-bottom gradient that white text sits on), and warn, bad
  and good. The palette is plain numbers, so `DotPaletteTests` asserts that every text token holds 4.5:1 on every ground
  in both looks, and the Live Activity draws with the same values.
- **Type** is SF through the system's text styles (`Theme.body` is `.callout`, `small` `.footnote`, `meta` `.caption`) so
  everything follows Dynamic Type; code is SF Mono. Rows and cards re-lay out at the accessibility sizes, and floating
  chrome (the composer, the capsule, the chips) is capped at a size where it still fits.
- **Space and radius** are two scales (`Space` 2 to 32; `Radius` 4, 8, 12, 16, 24): controls 8, cards 12, the owner's
  message and the approval card 16, the floating bar 26.
- **Glass is for floating chrome only**: the composer bar, the capsule over it, the agents' chips, the
  the compose and settings buttons of the title bar (iOS 26 draws them as glass), the jump-to-newest button, the approval card's Allow once and Deny, the pairing field. `dotGlass(in:)`
  is Liquid Glass (`glassEffect`) on iOS 26 and a thin material with a hairline before it; `dotButton(.glass|.prominent)`
  are `.glass` and `.glassProminent` on iOS 26 and capsule buttons before it; `DotGlassGroup` is a
  `GlassEffectContainer`. Message text, tool lines and long lists are never glass. With Reduce Transparency it is a solid
  raised surface, with Increase Contrast the hairline is stronger, and `dotAnimation` stays still under Reduce Motion.
  Sheets keep the system's own glass on iOS 26 (`dotSheet()`), and a title's subtitle and the soft scroll edges are
  iOS 26's own.
- **Behaviour of the look**: a working chat's avatar has a ring that turns, a waiting one an amber ring; a tool step has
  its tool's symbol that pulses while it runs; the send button is one symbol that changes (arrow, queue, stop); an
  approval is the loudest thing in a chat (an amber card with Allow and Deny as large glass buttons), and once answered
  folds into a quiet line; haptics are `sensoryFeedback` (an approval, a send, a stop, a switch, a selection).
- **Debug-only aids**: `-DotGallery 1|2|3|4` shows the pieces on one screen with made-up data (a step that asks, a
  secret, a failed step, the buttons, the empty states), and `-DotMaterialLook 1` turns the iOS 26 look off to see the
  look an iOS 18 phone gets, on an iOS 26 simulator.

Screenshots of the design pass, with the audit that led to it, are in `roadmap/1380.md`; the simulator shots were made
against a scratch daemon with a scripted model (the daemon of the screenshot section above) with
`xcrun simctl ui <device> appearance dark|light`, `content_size accessibility-extra-extra-extra-large`, and
`DotUITests/GlassShotsTests` for the screens that need a tap. No iOS 18 simulator runtime is installed on the Mac
that built this, so the fallback look was seen only through `-DotMaterialLook`, which shares the app's own code paths but
not the system's (a pre-iOS 26 navigation bar and sheet are the system's to draw).

## Build and install on the iPhone

1. `brew install xcodegen` once, then in this folder: `xcodegen generate`.
2. Open `Dot.xcodeproj` in Xcode. Under Signing & Capabilities of both targets the team is `79DZ4AA4DW` with
   automatic signing; sign in to that Apple account in Xcode › Settings › Accounts if Xcode asks.
3. Connect the iPhone with a cable, unlock it and tap Trust. On the iPhone, turn on Settings › Privacy & Security ›
   Developer Mode (iOS asks to restart).
4. Pick the iPhone as the run destination and press Run (⌘R). The first time, iOS may ask to trust the developer under
   Settings › General › VPN & Device Management.
5. In the app, type the Mac's tailnet address (`mac.your-tailnet.ts.net`), then type the pairing code Settings › Phone
   shows on the Mac (or pair in the web UI, "Pair in the web UI instead"). Tailscale must be on on the iPhone.

To change the address later: Settings › Change address in the app (or, in the web UI fallback, press with two fingers for
a moment, or use "Change address" on the error screen). Changing the address forgets the phone's key, since a key
belongs to the daemon that issued it.

The app tells the daemon the APNs environment its entitlement was signed with (`APS_ENVIRONMENT` in `project.yml`): `sandbox` (`development`) until a production key and profile exist.

Simulator check without signing (pick an installed simulator with `xcrun simctl list devices available`; the name or
`id=<udid>`):

```bash
xcodegen generate
xcodebuild -project Dot.xcodeproj -scheme Dot -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -configuration Debug CODE_SIGNING_ALLOWED=NO build
xcodebuild test -project Dot.xcodeproj -scheme Dot -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -configuration Debug CODE_SIGNING_ALLOWED=NO
```

The repository's CI does not build the Swift (its workflows are Python, JavaScript and Rust): run these on a Mac before a
change to `ios/` is merged.

The simulator gets no APNs device token and has no Keychain entitlement without signing; push and Live Activities need
the iPhone. To look at the app in the simulator, start a daemon on a scratch home (`DOT_HOME`, `DOT_LOG_DIR` and
`DOT_CHROME_DIR` at throwaway folders, as `tests/smoke/test_stream_last_event_id.py` does with a scripted model), pair a
key (`POST /api/phone/pairing`, then `POST /api/phone/pair`), and launch a Debug build with
`-DotDevOrigin http://127.0.0.1:<port> -DotDevKey <phone key>`; `-DotOpenChat <title prefix>`, `-DotShowInspector 1`,
`-DotShowSettings 1`, `-DotShowInbox 1` (the inbox of what waits on the owner) and `-DotShowSchedules 1` (with the settings) open a screen at launch, `-DotGallery 1|2|3|4` the design gallery, `-DotMaterialLook 1` the pre-iOS 26 look, `-DotOpenURL dotagent://run/<run id>` opens a link as
a Live Activity's tap does, and `-DotClearDrafts 1` starts with no drafts. These arguments exist in Debug builds only.

Taps and screenshots (slice 2): start a scratch daemon with a scripted model that streams, asks and lists models (the
way `tests/smoke/test_stream_last_event_id.py` does, with the pairing and a few chats; it writes
`{"origin": "http://127.0.0.1:<port>", "key": "<phone key>"}` to `/tmp/dot-ui-daemon.json`), uninstall the app from the
simulator once if the daemon is a new one (the cache holds the old one's chats), and run the `DotUI` scheme:

```bash
xcodebuild test -project Dot.xcodeproj -scheme DotUI -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -configuration Debug CODE_SIGNING_ALLOWED=NO
```

The simulator has no microphone: with the permissions given, a press of the microphone ends in "Dictation is not
available now" (the recognizer is not available there), which is the clean failure the tests check; listening needs a
real iPhone.

## The `DOT_HOST` build setting

`WKAppBoundDomains` must name the host at build time, while the owner types the host at run time. So by default the key
is left out and the app enforces the single origin itself: every navigation to another origin goes to Safari, frames
of another origin are dropped, and the bridge and the microphone answer only the daemon's own main frame.

Set `DOT_HOST` (project build settings, or `xcodebuild … DOT_HOST=mac.your-tailnet.ts.net`) to also have WebKit
enforce it: `scripts/app_bound_domains.sh` writes the host as the only `WKAppBoundDomains` entry, the web view turns on
`limitsNavigationsToAppBoundDomains`, and the address screen accepts only that host.

## What the app needs from the daemon

- The web UI at `https://<host>/` over Tailscale's HTTPS (no plain HTTP, no App Transport Security exception).
- After pairing (and on later loads, so a reinstall picks the key up again), the page posts
  `window.webkit.messageHandlers.dot.postMessage({type: "paired", key: "<phone key>"})`. The app accepts it only from
  the main frame of `https://<host>` and only a key shaped `phone.<id>.<secret>`. The page can tell it runs in the app
  by `DotPhone/<version>` in its user agent.
- The page's Notifications card posts `{type: "openSettings"}` (no data, item 1005), from the same main frame: the app
  opens its page of the iPhone's Settings, where its notifications are switched.
- The page posts `{type: "working"}` when the owner starts a run (item 1096): the app opens the Live Activity itself,
  which comes without a notification, unless one is up, after `DELETE /api/phone/apns/activity` with no token.
- `POST /api/phone/apns` with `Authorization: Bearer <phone key>` and
  `{"device_token": "<hex>", "start_token": null, "environment": "sandbox" | "production"}` (the daemon starts no
  activity by push, item 1096), sent again whenever the device token changes.
- `POST /api/phone/apns/activity` with the same header and `{"run_id": "fleet", "token": "<hex>"}` for the update
  token of the phone's one Live Activity (item 1093), and `DELETE /api/phone/apns/activity` once it was swiped away or
  ended (also sent when the app opens and has none).
- Live Activity pushes with `attributes-type` `DotFleetAttributes`, `attributes` `{fleetId}` and `content-state`
  `{active, rows}`, each row `{runId, title, agent, status, kind, line, startedAt}` (`status`: `working`, `waiting`,
  `done`, `failed`; `startedAt` in epoch seconds), at most four. Topic `dev.dotagent.phone.push-type.liveactivity`.
- An alert carries `route` (a web UI route such as `#/runs/<id>`, or an absolute path on the daemon) at the top level of the payload; a tap
  opens it in the web view.

## The app icon

`scripts/make_icon.swift` draws the icon (item 1382): a luminous blue dot with two dark eyes, lit from the top left, on a
near-black navy ground with a glow of the accent, in the three looks iOS 18 asks for (`icon-1024.png`,
`icon-1024-dark.png`, `icon-1024-tinted.png`), and the launch screen's mark (`LaunchMark.imageset`, the same dot on
transparency, shrunk with `sips -z 360 360`; the launch screen is that mark on `LaunchBackground`, the app's ground in
both looks): `swift scripts/make_icon.swift Dot/Assets.xcassets/AppIcon.appiconset`. The global accent colour
(`AccentColor`) is the app's blue, and the Live Activity draws with `DotPalette` and the same mark.
