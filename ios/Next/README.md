# DotNext: the rewrite of Dot for iPhone and iPad

The native app that replaces `ios/Dot` wave by wave (the plan lives in the owner's workspace,
`ios-rewrite-2026-11-24/PLAN.md`). It installs beside the old app: bundle id `dev.dotagent.phone.next`, app group
`group.dev.dotagent.phone.next`, its own Keychain access group, URL scheme `dotnext://`, display name "Dot Next". iOS 26
and later, iPhone and iPad. No push and no Live Activity until the swap (owner's decision): the old app keeps them.

Wave N0 (this tree today) is the design system, the components, the gallery and the app shell with placeholder
content; it reads no daemon. The engine comes in N1-client.

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
```

## Screenshots

`scripts/shots.sh <DotNext.app> <out dir> "tr en" <booted simulator udid>...` captures every scene (the gallery pages,
the tabs, the chat and its sheets) in light and dark, at Large and AX3. The debug build opens a scene with
`-DotNextScene <name>` (`LaunchScene.names`) and writes `Library/Caches/dotnext-ready.txt` once it is drawn; the script
waits for that file before each capture.

## The icon

`swift ios/Next/scripts/make_icon.swift ios/Next/Resources/Assets.xcassets/AppIcon.appiconset`: a flat graphite tile
with a light disc and two dark eyes (owner's decision D12), in the default, dark and tinted looks.
