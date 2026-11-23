# Dot for iPhone

A native SwiftUI app for Dot (items 997 and 1353): the chat list with the agents strip, chats that stream, the
approvals and questions a run asks, the helpers it waits for, the queue, the schedules and loops, and the models, all
over the daemon's own API; plus what a web page cannot do on iOS: remote push and the phone's one Live Activity of the
owner's runs on the lock screen and in the Dynamic Island. The web UI stays as it is: the app opens it full screen from
Settings › Open web UI (and to pair in the page), but it is no longer the app's main screen.

- `Dot/`: the app (`dev.dotagent.phone`).
  - `Core/`: pure rules and records, no UI: address parsing, navigation policy and bridge validation of the web
    fallback; the daemon's records (`Models`, `JSONValue`); `ChatState`, the reducer that turns runs and events into
    rows; `QueueOrder`; `MessageBlocks`.
  - `Data/`: `APIClient` (URLSession over `DaemonHost`, the phone key from `PhoneKeyStore`), `SSEParser` and `EventFeed`
    (resume from the last event id, backoff, reconnect on foreground and on a change of the network), `ChatCache` (JSON
    files per host), `ChatSession` (one open chat, an actor), `NativeStore` (the list, the lifecycle stream).
  - `Features/`: the screens: `ChatListView`, `ChatView` with `Composer`, `QueuePanel` and the status line,
    `RunInspector`, `SettingsViews` (models, schedules), `PairView`, `Theme`.
  - `Adapters/` the Keychain, UserDefaults and the push/share HTTP client; `Push/` the token registration;
    `Views/` the address screen and the web UI fallback (`DotWebView`).
- `DotTests/`: the unit tests (XCTest) of the reducer, the SSE parser and feed, the cache, the queue order, the link
  style and the requests.
- `DotLiveActivity/`: the widget extension (`dev.dotagent.phone.live`) that draws the Live Activity.
- `DotShare/`: the share extension (`dev.dotagent.phone.share`, item 1009): what another app shares goes to Dot as a
  new chat. It reads the host from the app group's defaults and the key from the shared Keychain access group.
- `Shared/DotFleetAttributes.swift`: the activity's attributes, compiled into both targets.
- `project.yml`: the XcodeGen spec. `Dot.xcodeproj` is generated from it and is not committed.

## What the native app does, and what it leaves to the web UI

Native: pairing with the code of Settings › Phone, the chat list (working, waiting on you, unread; pull to refresh; drawn
from disk at launch), a chat (messages that stream in, a tool step as one line, approval and question cards with Allow
and Deny, one line over the composer: working, waiting on you, waiting on N subagents; the queue, each message with an up
and a down arrow and a remove button; Send queues while a run works, and Stop with an empty box; a photo to attach), the
run inspector (the run, its plan, its helpers, the chat's schedules and loops with their switches), the Schedules list and
the models (the default and the review model) in Settings, notifications and the Live Activity. In the web UI: everything
else (new chats, agents and teams, memory, rules, settings, the vault, older messages, a step's details) until those
screens come; the plan is in `roadmap/1353.md`.

How it stays fast: a chat read before opens from memory in its first frame, otherwise from `ChatCache` (the newest runs
and the last 400 stored events), then asks only for the events after the last id it holds; reading, reducing and writing
happen in `ChatSession`, off the main actor, which hands the screen a new `ChatState` at most every 40 ms while words
stream. The run's stream is followed with `after=<id>` and `Last-Event-ID: <id>` (item 1352), so a reconnect reads no
history again; it comes back at once when the app comes to the front or the network changes, and otherwise after 0.5 s,
1, 2, 4, 8 and 15 s. The transcript follows new words only while the reader is at the bottom.

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
`-DotShowSettings 1` and `-DotShowSchedules 1` open a screen at launch. These arguments exist in Debug builds only.

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

`scripts/make_icon.swift` draws the placeholder icon (a yellow circle with two eyes):
`swift scripts/make_icon.swift Dot/Assets.xcassets/AppIcon.appiconset/icon-1024.png`.
