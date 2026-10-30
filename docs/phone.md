# Dot on the phone

Without Tailscale, Dot can also be reached from the phone's own Messages, in the conversation you have with yourself:
see [Channels](channels.md). By voice, where the phone's browser offers it (Safari does; an app added to the home
screen may not, and the page says when the browser refuses): the composer's microphone dictates a message, and the
speaker under an answer reads it aloud with the browser's own voice, in the language chosen in Settings › General ›
Voice (item 574; see [the daemon page](daemon.md#the-daemon-the-web-ui-and-the-service)). Safari's own speech
recognition hears the first dictation of a page and at times none after it, so on an iPhone or an iPad the microphone
records your voice itself and your Mac writes it down, on the device, every time (item 626): the Mac needs macOS 26 or
later and Apple's developer tools (`xcode-select --install`), which the page asks it once as it starts. The first time a
language is used the Mac downloads its dictation model from Apple, so that first one takes longer; the voice goes to
your Mac and nowhere else ([Safety](safety.md)). A Mac that cannot leaves the microphone Safari's own, where a later
dictation Safari does not hear says so, names what the Mac lacks, and Reload brings the microphone back (item 616).
Under each message of Dot's a smiley opens the palette of reactions, a thumb's size, and what each does is the same on
the phone: a 👎 on a run at work reaches it, a ✋ holds it until you write, a 📌 keeps the message as a note (item 586; see
[Reactions](runs.md#reactions-on-dots-messages)).

The web UI is a progressive web app: open it on the phone, add it to the home screen, and it opens on the chat you
were in last (New chat the first time, and when that chat is gone; items 286 and 810), with the sidebar's sections, the
chats and the run pages at a phone width. Back from the background, it follows the daemon again at once, and says it is reconnecting only when that takes
more than two seconds. Its colours follow the light or dark appearance
through CSS `light-dark()`, so it needs iOS 17.5 or later (Safari 17.5; Chrome 123 and Firefox 120 on a computer). It
needs to reach the Mac, and a push needs a secure address, so the way there is your own network:

Settings › Phone (items 271, 421 and 565) guides you there a step at a time. Its first card says whether the phone
reaches Dot, **Ready: your phone reaches Dot** with the name it reaches Dot by, or, while a step is left, how many
of the three steps on this Mac are done (*2 of 3 steps done on this Mac*, in words and in a track of three), and holds
those steps side by side, each saying where it stands and offering what does it; it reads Tailscale again every few
seconds while a step is left, so that it follows an install or a sign-in. A step done has a tick, and the one to do now
has its number marked (item 527).
**Tailscale on this Mac**: while it is not installed, **Get Tailscale** opens its
page in the Mac App Store, where a click installs it; while it is installed but not running signed in, **Open
Tailscale** opens its app, which shows its sign-in (`POST /api/phone/tailscale`); when Tailscale cannot be asked, its
app closed or a call that failed, the step says what it said. **Dot on your Tailscale network**: **Set up** has `tailscale serve
--bg <port>` present Dot over HTTPS and writes the Mac's name into `[server] reach` (`POST /api/phone/tailnet`, 422 with
why when Tailscale is missing or not signed in); when the tailnet has HTTPS off, Tailscale names the page that turns it
on, and the step offers it as **Turn on secure connections**, then Set up again. **Dot answers to the name**: the
daemon answers to it from its next start, and **Restart Dot** starts it again once you say yes, and says when runs
going on would stop with it (`GET /api/phone` reads the state). The card's switch **Keep this Mac awake while it
charges** (`[server] keep_awake`, `POST /api/phone/awake`) has the daemon hold macOS's `/usr/bin/caffeinate -s`, which
keeps the Mac from sleeping on power while the daemon runs; a closed lid still puts a MacBook to sleep. Under it, **Set
up a phone** has the phone's four steps (install Tailscale on it, open Dot in Safari, add it to the home screen, sign in
with a code) beside one QR code that a choice, **Tailscale app** or **Open Dot**, sets to Tailscale's download page for
the phone or, once Dot answers to its name, to its address (also there to type, or to **Copy**), both made by macOS's
own generator (`GET /api/phone/qr/tailscale` and `/dot`); the step marked is the one whose code is shown, and the last
once a pairing code is. **Sign-in code** keeps a row of dots in the code's place until **Show code** makes one (`POST
/api/phone/pairing`): good for ten minutes and for one use, it signs in the app on one phone's home screen, and **New
code** takes its place. The same by hand:

1. The daemon stays on the loopback address. With Tailscale on the Mac and the phone, `tailscale serve --bg 8765`
   presents it as `https://<mac>.<tailnet>.ts.net`, with a certificate of its own, to your tailnet only.
2. Put that name in `[server] reach = ["<mac>.<tailnet>.ts.net"]` and restart the daemon: a request with that `Host`,
   and a page from `https://` that name, is served like a local one. `dotx url` then prints the signed-in address for
   the phone too (the token stays in the fragment). Settings › Phone lists the address (item 267).
3. On the phone, Settings › General › Notifications on (on an iPhone in Dot added to the Home Screen and opened from
   there, where alone iOS lets a page take notifications; its Safari tab says so, item 501): the browser subscribes to
   the daemon's web push, its subscription taken as the browser sends it (`expirationTime` is iOS's to send and Dot
   keeps nothing of it, item 548), and from then
   on the daemon tells the phone what the desktop notifications say, in the same words, page open or closed, and while
   you look at that run on the phone too: the rule that keeps a notification quiet while you look at its run holds for
   the notifications of a page, not for a push; a tap opens what it tells of. The phone also hears what no page can, as
   it happens before a page is back: the daemon started again after it did not stop cleanly (it was killed, crashed or
   ended with an error; it leaves `<home>/daemon.running` while it runs), with how many runs that cut off. The daemon
   signs the pushes with a key pair it makes on first start (`<home>/vapid-key.pem`); subscriptions live in the store
   and go when their push service says they are gone; a service that is busy or failing (429, 5xx) is asked once
   more a second later. A subscription whose keys cannot encrypt a message is refused
   (422), and one stored before that check is dropped with a warning while the others still get theirs. Settings ›
   General › Notifications off drops the subscription.

On a phone the page holds still (item 275). Every page fits the screen's width: a long link, path or word breaks, and
code and tables scroll in their own frame. No page lets the phone zoom into the field that gets the focus: on an iPhone
and an iPad the viewport allows no scale past the first, so a field has the size it has on the Mac (item 311), and on
another touch screen no text field is under 16 px. A double tap does not zoom, and the page does not stretch past its
ends. The app added to the home screen runs to the screen's edges, and
the bar, the sidebar, the composer and the side panel keep out of the rounded corners, the notch and the home
indicator. The bar stays at the top of what is seen, also while the keyboard is up, and the composer, the drawers and
the dialogs stand on what is seen too: iOS 26 can leave the page panned once the keyboard has gone, which left a band
under the composer and the tops of the drawers under the status bar (item 293). On a touch screen a page leaves the
caret for a tap as it opens, so the keyboard comes only when you ask for it.

A phone that comes back to Dot finds it as it left it (item 328). Back from the background, the page makes its
connections again without a word for 8 seconds, as long as a phone's network (a VPN such as Tailscale) takes to wake,
counted from the return even for a connection that broke while the page was away (item 709);
only a connection still not there then shows the bar's pill and, on a chat's page, a note that floats under the bar
over the conversation, so that nothing read moves as it comes and goes. What changed meanwhile is read again, and only
what changed is drawn again: the sidebar and the helpers keep the rows that did not. Opened again after iOS let it go,
Dot does not wait for the network either: the service worker answers from its copy (below), and while the page waits
for the daemon a curtain shows the chat it opens on, its bar and its composer with the draft, where the chat's page has
them. The curtain lifts once the chat is there, and says that Dot connects only after those 8 seconds. The browser's bar
(the status bar of an app on the home screen, Safari's tab bar) takes the colour of the page's ground, in the theme and
the scheme on screen.

On a phone Dot reads at a phone's size and takes a finger (item 329). On a touch screen of a phone's width (720 px and
under, or under 500 px high on its side) the body is 16 px, as iOS's own text, a hint 15 px, and only the times, badges
and the like are 13 px; Settings › Appearance's Text size still scales them all, and code keeps the size chosen
for it. A phone's buttons and fields are 36 px tall and the small ones 32 (an icon button answers a finger's 44 px around it, and a button keeps its words, item 787), the heads of the steps and the rows of a
delegated run keep 40, and what sits in a line (a chip, a way to a page) 32; a link inside a line of text is no target
of its own. A switch's 40 by 24 px track sits in a 48 px box (item 337); a tap anywhere on a setting's row turns its
switch, and a step's duration shows without a hover. An iPad and a Mac keep their
sizes. Where a page names a setting of the config file, it says what it is in words, the key after it in faint type.

Every screen was looked at on a phone, shot a screen at a time at 390 by 844 (item 606): what is tapped without being a
button is 32 px or more (the tabs of the details, the name of a file a run changed, the star of a model, a licence's
notice); a toast stands under the bar, across the page, and never over the composer, its send button or the stop button
of a run at work; a sentence with a button beside it (the token this browser keeps, Appearance's two choices that follow
the theme) gives the button a line of its own; a note the owner decides on (a forgotten one) has its buttons under its
words; the buttons of a version in Self-improvement wrap rather than reach past the edge of a phone 320 px wide; the
file's panel keeps the room for the file's name, and Open on the Mac is its icon; Add and Cancel in a service's key form
have space between them; a ready team the owner has already says so under its words, which have the card's width; and
the name of a plugin is not cut by an ellipsis while there is room for it, which WebKit did to every name of two words.

On a phone the settings open on a list of their seven sections (items 330 and 845): General, Appearance, Models, Rules
and memory, Browser and Vault, Phone and channels and System, each row a section's name and what it holds (the theme on
screen, the main model, how many notes there are, how many items the vault holds). A row opens its section on its own,
the bar naming it and the row of the section's tabs under it, and a card's name stands over the
card, as a section's title does on the redrawn tabs (item 576); a card that folds keeps its name on its ground. In a
Mac's window the sidebar is the settings' nav while they are open, the same seven sections (item 562); see
[the daemon, the web UI and the service](daemon.md#the-daemon-the-web-ui-and-the-service). On a phone
Appearance draws the colour schemes as phones and puts the themes two a row, and its Text size starts at a phone's 16 px
(item 331). A service's page of Customize › Connectors and an agent's editor name their cards the same way, a row of the
home's Today strip keeps its facts to one line that fades where it runs out of room, and Handoffs' empty page says what
brings messages there (item 332).

At a phone's width (720 px and under) Dot is laid out like a phone app (items 294 to 298). The sidebar is the
list of chats over the whole screen, Dot's name at its head, and the cog of Settings, Connect apps (item 492), the search and the **+** of a new chat at its foot, under
the thumb. A chat's bar has a way back to that list, the chat's title over the agent and the model, and its tools
in one pill: Dot's computer once the run has had a page, the helpers with their count, and the details. The line over the
composer is one bar of glass (item 401): how long the run has worked (or that it waits for you), how its helpers stand
and how many messages wait in the queue at its left, each part opening what it tells of, and the way down at its right
end, in the same pane; it is a row of 40 px, a translucent surface with the page blurred under it, a hairline and a
rounded pill (items 352 and 360; a wider window has the same bar), as wide as its parts and the way down and no wider,
in the middle of the dock, not across all of it (item 427).
A list row (Campaigns, Schedules, agents, notes) is its face or switch, its title and its buttons on one line, and
what else it says (chips, text, times) under them across the row's whole width; a card's head with a line beside its
name puts the line under the name (item 436). The conversation keeps a chat's rhythm (item 401):
a turn after a turn keeps 8 px and the rows of what a run did 4, a message's bubble holds its words in 4 px above and
below, a step's head is 32 px tall with a call's row 34 px from the next and no empty line under it (item 438), so a
phone shows more of what the run did without shrinking the prose. A chat is one screen (item 352): the bar
and the composer stay where they are and only the conversation scrolls, so a scroll, its bounce at either end and the
keyboard move nothing else. The composer floats over the conversation's foot as a pane of glass, with no band of the
page's colour behind it, and the conversation scrolls under it, its last line kept clear of the bar however tall the
composer grows (items 374 and 401); the "+" and a compact square stop or the send stand beside the box at 36 px, and the
choices on the line under it. The conversation scrolls up and down only
(item 360): a table or a block of code wider than the screen scrolls sideways in its own box, and a step's command or
tool name stays on its row's one line, cut by an ellipsis (items 512 and 513). A delegated run's row, under the helpers it belongs to, is one line tall at this width:
its face, its goal, its state and time, and the way to its page, with the goal or the last line cut by an ellipsis
rather than wrapped onto a second row (item 401); the helpers' own head is one line too. The details rise from the foot
as a sheet with a grabber, and a drag of its head down closes it; its Agents tab says of each helper its state, its
model and what it does now (on a wide window too). The Screen tab with nothing yet says in the same empty panel as the
other tabs that Dot's computer is quiet and that the screenshots it takes while it works will appear there, with the
setting that turns it on. Dot's computer opens over the whole screen, with the page's address and who has control (Take
over), and goes back to the side panel when it closes. **Use it here** holds the run and uses its page from the phone
(item 475): a tap clicks, a drag scrolls the page, a finger held still and then dragged drags on it, two fingers zoom
the picture, and **Keyboard** types into the page; the card of a browser step handed to you offers it too, and **Return
control** gives the run back and closes the view (item 491). A tab a tap opens is not the one the picture shows.

The button at the top left is the one way back (item 338): a chevron on the page's own ground, no circle around it
(item 373), in a 36 px box. On a chat's page, wherever it was opened from, it opens the list of chats in place, as the
sidebar comes over the page: the address stays the run's, and no page of chats stands between them (item 401). On a
delegated run's page it says Back and leads to the run that started it. On any other page opened by a link of another
page (a service from Connectors, a tab from the settings' list) it says Back and returns to that page; on a page opened
directly that has one above it (a settings tab, a service's page, an agent's, a team's or a campaign's page, Handoffs) it
leads there; on a page opened from the sidebar it is the way back to the list of chats. The browser's own Back and
Forward and a reload keep it as it was, and it is reckoned again when the phone turns. A chat that goes on at another of
its runs (a follow-up, the queue's next message, Continue, an answer, a rewind) stays the same page: it keeps its way to
the chats, and the browser's Back skips the run it went on from (item 345). The pages' own links back show in a wider
window only.

An app added to the home screen keeps storage of its own, apart from Safari's (iOS does so), so it has none of the token
that the signed-in address gave Safari, and its sign-in screen asks for a sign-in code instead (item 267): Settings ›
Phone, on the Mac or in Safari where Dot is open with the Mac's own key, shows one with **Show code**, eight characters
in two groups (Crockford's base32, typed in any case, with or without the dash), good for ten minutes and for one use; a
new code takes the place of the one before, and five wrong tries use a code up. The app trades it for a key of the
phone's own (`POST /api/phone/pair`, the one route besides the health check and the proof that takes no token; the Host
and Origin checks hold for it as for any other; item 670): `phone.<id>.<secret>`, which the daemon keeps nowhere: it
keeps the key's SHA-256, the phone's name (iPhone, iPad, Android phone, read from its browser), and when it was paired
and last used, in `<home>/phones.json`, readable by you only. The phone proves the daemon with its key as with any token
(the proof is keyed with the key's hash, the one thing the daemon holds of it). The daemon is not proven before a code
goes, so the screen sends nothing that cannot be a code: a token pasted into the code's field stays on the phone. A
code is as good as a key while it lasts: whoever reads it and reaches the port first is let in, so it is asked for when
the phone is at hand, and a phone that the code then refuses means that someone else used it. `POST /api/phone/pairing`
gives a code, `GET /api/phone` the addresses. Neither the code nor a key is logged. Twenty phones at most hold a key;
the twenty-first pairing is refused until one is removed, and its code stays good. A `phones.json` the daemon cannot
read keeps it from starting, and says so: a fault is never taken for no phones, which would pass for a removal; deleting
the file unpairs every phone. The phone reaches Dot while the Mac is on and awake.

Until it is removed, a phone's key is as strong as the Mac's own key, but for the ways into Dot: it cannot make a
sign-in code, nor see or remove the paired phones, nor switch the iMessage channel or pair or remove a number of it
(403), so a phone that is taken cannot open another way in, nor close yours. Nor can it widen for good what runs do
without asking, which would outlast its removal (item 678, 403): add a connected app (a server Dot starts runs its
command at each start of the daemon), or one another agent of the Mac set up, switch Dot's safe box for commands or its
extra checker (the typed judge) off, add or switch on a rule that lets steps through (allow, pre-approved), delete or
switch off a rule that asks, or move any rule; what narrows (a rule that asks, the safe box on) it still does. What is
shown or connected on the Mac alone (the eye of the vault, 1Password, a sign-in of Google or Microsoft, the microphone's
pane of System Settings) answers the installation's token alone, never a phone's key, whatever address a phone's request
names. `yolo`, which asks nothing, is yours to use from the phone as from the Mac: a phone's key can start a run, a chat
or a campaign in `yolo`, make a schedule, a loop of a chat (`/loop`) or an agent kept in `yolo`, switch one on or let
one go on, and what such a run does is not held by these limits (it may do whatever your account may). Removing a phone
does not undo what it did: a phone that may have been used against you is removed, the installation's key made new as
well (below), and what it may have changed looked over (schedules, campaigns, agents, Settings). On a phone, Settings
› Phone says so and has no Show code, and Settings › Channels says where to pair a number. The paired phones' file is one of Dot's own files, which a run's commands and file
tools can neither read nor change in every mode but `yolo` ([Safety](safety.md)).

A phone that is lost, or that should no longer reach Dot: **Settings › Phone › Paired phones** lists each phone that
signed in with a code, with when it was paired and last used, and **Remove** signs it out at once (`DELETE
/api/phone/phones/{id}`): its key is refused from its next request, the streams it has open end, and it is back at the
sign-in screen, where a new code is needed. A phone paired before item 670 holds the installation's own token, which
does not expire, and is not in the list. Take such a phone off your tailnet in Tailscale's admin console, which cuts it
off at once. To make the token it holds worthless too, delete `<home>/api-token` and run `dotx service restart`: the
daemon makes a new token, every browser and phone signs in or pairs again (`dotx open` opens the web UI signed in), and
the desktop app reads the new one from the file. `tailscale serve reset` stops presenting Dot on the tailnet, and taking
the name out of `[server] reach`, then restarting the daemon, stops it answering to that name.

Added to the home screen, the app carries its manifest: `id` `/`, so that a phone that adds it again keeps one app and
not two, and the light scheme's own ground, `#f8f7f4`, as its `theme_color` and `background_color` (the bar and the
screen between the tap and the first frame, the colour the page gives the browser in the light scheme), so that no white
flash or strip of amber shows while it opens. Beside the plain icons it carries a 512 px `maskable` one, its artwork at
four fifths of the square on that ground, which a launcher that rounds or crops an icon draws whole; a long press on it
offers **New chat** (`/#/runs`) and **Settings** (`/#/settings`). Since iOS 26, which opens what is added to the Home
Screen as a web app, iOS takes the added app's icon from the manifest, which therefore names only PNGs of a size each,
the 180 px touch icon among them (item 661): an SVG of any size there outranked every picture and is not drawn, so an
iPhone added Dot with the letter D. Before, iOS drew it with one of the page's `apple-touch-icon` files (items 410 and
550), which the page still names: 180, 167, 152 and 120 px under the icons folder (`/icons/touch-icon-180.png` and its
siblings), which the page names at absolute paths, so that a path or a cache of an older icon cannot leave it with the
letter iOS draws for an address that has none. A web clip added before an icon changed keeps the icon it first drew
until it is removed and added again; iOS keeps that drawing of the app itself. The 180 px files at the root
(`/apple-touch-icon.png`, its `-precomposed` twin and the `180x180` names) stay for the names iOS tries by itself when a
page names none. A phone's own process fetches the icon outside the page, so those files, the manifest, the favicon and
the icons folder are served with a resource policy that lets another origin read them, and a name of `[server] reach`
written with HTTPS's own port (`:443`) names this server too. A request the daemon turns away is logged
(`request_refused`, with its path, status, reason, Host and Origin, never the token), so that an icon that does not come
says why in `dotx.log`.

The daemon answers with gzip what a browser asks it for that way (item 381): the pages' scripts and sheets and the API's
answers, past 1 KiB; the event streams go as they are written, and the fonts and the images, compressed already, as they
are. The page names the modules its script reaches on opening (`modulepreload`), so a phone asks for them at once
instead of one import after another; a page that is not the chat or the home loads its script when it is first asked
for, and the browser fetches those scripts once it is idle, so that the first one asked for opens at once (item 388).

The service worker (`sw.js`) keeps the shell files as a fallback for when the Mac is out of reach and caches nothing
under `/api`. It keeps them by the build of the pages they are of, which the daemon names in the `X-Dot-Pages` header of
every file, one build at a time (item 328): a page opened while the network has not answered within 200 ms is answered
from that copy, every file of it from the same copy. When the network's answer, once it comes, names another build, the
next opening waits up to 5 seconds for the network, and keeps that build whole (item 359), so that a phone whose network
is always slower than 200 ms takes an update all the same; the copy answers until then, and when the network fails or
does not answer within those 5 seconds. A page of another build than the daemon's loads again before it shows anything,
so that it never meets an API it does not know. A page loaded from them while the daemon is out of reach says so and
tries again by itself (item 200): after 2 seconds, then twice as long each time up to 30, and at once when the browser
is back on the network or the page back in view, until the daemon answers. A page opened when the network fails and no
copy was kept of it (the first opening from the home screen after the app is added, say) is answered with
`offline.html`, which the service worker keeps as it installs: it says that the Mac cannot be reached, and to check that
it is on and that Dot runs on it, then to try again. A script or a font that cannot be had is still an error. Without
Tailscale, the same works on the Mac itself (`localhost` counts as secure) and on nothing else.
