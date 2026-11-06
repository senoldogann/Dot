# Dot for iPhone

A thin native shell around Dot's web UI (roadmap item 997): a full-screen `WKWebView` that loads only the daemon's
tailnet address, plus what a web page cannot do on iOS: remote push and a Live Activity of a run on the lock screen and
in the Dynamic Island.

- `Dot/`: the app (`dev.dotagent.phone`). `Core/` holds the pure rules (address parsing, navigation policy, bridge
  validation), `Adapters/` the Keychain, UserDefaults and the daemon's HTTP API, `Push/` the token registration.
- `DotLiveActivity/`: the widget extension (`dev.dotagent.phone.live`) that draws the Live Activity.
- `Shared/DotRunAttributes.swift`: the activity's attributes, compiled into both targets.
- `project.yml`: the XcodeGen spec. `Dot.xcodeproj` is generated from it and is not committed.

## Build and install on the iPhone

1. `brew install xcodegen` once, then in this folder: `xcodegen generate`.
2. Open `Dot.xcodeproj` in Xcode. Under Signing & Capabilities of both targets the team is `79DZ4AA4DW` with
   automatic signing; sign in to that Apple account in Xcode › Settings › Accounts if Xcode asks.
3. Connect the iPhone with a cable, unlock it and tap Trust. On the iPhone, turn on Settings › Privacy & Security ›
   Developer Mode (iOS asks to restart).
4. Pick the iPhone as the run destination and press Run (⌘R). The first time, iOS may ask to trust the developer under
   Settings › General › VPN & Device Management.
5. In the app, type the Mac's tailnet address (`mac.your-tailnet.ts.net`), then pair in the web UI as before
   (Settings › Phone code). Tailscale must be on on the iPhone.

To change the address later: press with two fingers on the web UI for a moment, or use "Change address" on the error
screen. Changing the address forgets the phone's key, since a key belongs to the daemon that issued it.

A build from Xcode registers its tokens with `"environment": "sandbox"`; a Release build says `production`.

Simulator check without signing:

```bash
xcodegen generate
xcodebuild -project Dot.xcodeproj -scheme Dot -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -configuration Debug CODE_SIGNING_ALLOWED=NO build
```

The simulator gets no APNs device token; push and Live Activities need the iPhone.

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
- `POST /api/phone/apns` with `Authorization: Bearer <phone key>` and
  `{"device_token": "<hex>", "start_token": "<hex>" | null, "environment": "sandbox" | "production"}`, sent again
  whenever either token changes.
- `POST /api/phone/apns/activity` with the same header and `{"run_id": "<run id>", "token": "<hex>"}` for each
  activity's update token.
- Live Activity pushes with `attributes-type` `DotRunAttributes`, `attributes` `{runId, agentName}` and
  `content-state` `{title, status, line, startedAt}` (`status`: `working`, `waiting`, `done`, `failed`; `startedAt` in
  epoch seconds). Topic `dev.dotagent.phone.push-type.liveactivity`.
- An alert carries `route` (a web UI route such as `#/runs/<id>`, or an absolute path on the daemon) at the top level of the payload; a tap
  opens it in the web view.

## The app icon

`scripts/make_icon.swift` draws the placeholder icon (a yellow circle with two eyes):
`swift scripts/make_icon.swift Dot/Assets.xcassets/AppIcon.appiconset/icon-1024.png`.
