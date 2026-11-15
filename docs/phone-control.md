# Dot and your iPhone

With `[phone] enabled = true` Dot keeps a link to your iPhone. The phone runs WebDriverAgent, an XCUITest runner app
(`[phone] runner_bundle`), whose server on port 8100 Dot reaches at the first of `[phone] wda_urls` that answers (for
example the Tailscale address, then the home network's). A run has one tool for it, `phone_status` in the group `phone`:
a fresh look at whether the phone answers and whether it is locked, with the address that answered. The prompt names the
phone and its state at the keeper's last look to a run that has the tool.

Dot neither reads the phone's screen nor acts on it yet. The tools that read the screen and tap, type and open apps come
once every rule the Mac's desktop keeps holds on the phone too: a password manager's screen never read, Dot's own app
and web UI never touched, and a tap, a key or a line break that sends a message, pays or posts asked for as on the Mac.
`phone_status` says only what Dot knows of the link, never anything the phone shows, so it marks no run as one that
read outside content.

## Keeping it reachable

The daemon keeps WebDriverAgent up. Every `check_every_s` seconds it asks `/status` and `/wda/locked`, which answer
without a session, locked or not. When no address answers and `xcrun devicectl list devices` lists the phone
(`[phone] udid`) as available, it launches the runner app again with `xcrun devicectl device process launch --device
<udid> --terminate-existing <runner_bundle>` and looks for its server for 40 seconds, the time its looks take counted;
a launch that fails, or whose server stays silent (a phone out of the Mac's reach), is not tried again for five
minutes. An address that answers but does not say whether the phone is locked (a refusal, or another device at that
address on the home network) leaves the link *unclear*, and what it answered goes to the daemon's log, never to a run.

A phone that stays locked, unclear or out of reach for `notify_after_s` seconds is told to you once for that state, as
any other notice of Dot's; a tap on the notice opens Settings › Phone, whose first card, while `[phone]` is on, says
how the link stands and for how long. Keep the phone unlocked while it charges: Settings › Display & Brightness ›
Auto-Lock › Never, with Low Power Mode off (it holds Auto-Lock at 30 seconds). `GET /api/iphone` gives the state of
the last look.
