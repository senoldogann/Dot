# Dot on the phone

Without Tailscale, Dot can also be reached from the phone's own Messages, in the conversation you have with yourself:
see [Channels](channels.md). By voice, where the phone's browser offers it (Safari does; an app added to the home
screen may not, and the page says when the browser refuses): the composer's microphone dictates a message, and Read
the answer aloud in an answer's sheet (hold the answer, item 1064) reads it with the browser's own voice, in the language chosen in Settings › General ›
Voice (item 574; see [the daemon page](daemon.md#the-daemon-the-web-ui-and-the-service)). Safari's own speech
recognition hears the first dictation of a page and at times none after it, so on an iPhone or an iPad the microphone
records your voice itself and your Mac writes it down, on the device, every time (item 626): the Mac needs macOS 26 or
later and Apple's developer tools (`xcode-select --install`), which the page asks it once as it starts. The first time a
language is used the Mac downloads its dictation model from Apple, so that first one takes longer; the voice goes to
your Mac and nowhere else ([Safety](safety.md)). A Mac that cannot leaves the microphone Safari's own, where a later
dictation Safari does not hear says so, names what the Mac lacks, and Reload brings the microphone back (item 616).
A message shows no buttons under it on the phone (item 1064), only the reactions that stand on it: hold a message (about
half a second) and a sheet rises from the foot of the screen, with when it was sent and how long its work took, the
seven reactions of Dot's messages, a thumb's size each, and what the message can do, a row each: copy it, select its
text (a hold no longer selects it; the words selected so take the phone's own Copy menu), read it aloud, show its
steps, edit from here (your own), open its task. Every message can be copied there: what Dot said between steps, and
your message while its run works, whose controls are kept back, have Copy the text (item 1226). A press
outside the sheet, a swipe down or Escape closes it. A reaction does on the phone what it does elsewhere: a 👎 on a run
at work reaches it, a ✋ holds it until you write, a 📌 keeps the message as a note (item 586; see
[Reactions](runs.md#reactions-on-dots-messages)).

The web UI is a progressive web app: open it on the phone, add it to the home screen, and it opens on the chat you
were in last (the home the first time, and when that chat is gone; items 286 and 810), with the sidebar's sections, the
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
   the notifications of a page, not for a push; a tap opens what it tells of (item 1070: Dot comes to the front and opens the chat that asks, or the page the
   notification is about, itself; with no window of Dot open, Dot opens there). The phone also hears what no page can, as
   it happens before a page is back: the daemon started again after it did not stop cleanly (it was killed, crashed or
   ended with an error; it leaves `<home>/daemon.running` while it runs), with how many runs that cut off. The daemon
   signs the pushes with a key pair it makes on first start (`<home>/vapid-key.pem`), naming the project's page as
   who sends them (Apple refuses an address it cannot reach, item 992; the signing library's own check, narrower
   than RFC 8292, is off so that it takes a page's address, item 1019), and logs what a push service that refuses one
   answers; subscriptions live in the store
   and go when their push service says they are gone; a service that is busy or failing (429, 5xx) is asked once
   more a second later. A subscription whose keys cannot encrypt a message is refused
   (422), and one stored before that check is dropped with a warning while the others still get theirs. A subscription
   is taken only at a push service of a browser's maker (item 1311): `[notify] push_hosts` lists them, by default
   Apple's (`web.push.apple.com`, `*.push.apple.com`), Google's (`fcm.googleapis.com` and `android.googleapis.com`, by name: a `*.googleapis.com` entry would take Cloud
   Storage buckets, which the caller names),
   Firefox's (`updates.push.services.mozilla.com`, `*.push.services.mozilla.com`) and Windows' (`*.notify.windows.com`);
   an address anywhere else is refused (422) with the host and this setting named, for a phone's key as for the Mac's
   token, because Dot posts each push, signed, to that address. A browser on a push service that is not listed is one
   more line in `[notify] push_hosts` (your list replaces the packaged one, so keep the defaults you still use). A
   subscription stored at a host that is not on the list (before this check, or a host taken off) is not sent to, and
   is kept, with a warning in the log. A subscription made with a phone's own key belongs to that phone and goes when
   the phone is removed, as its APNs device does; one made with the Mac's token belongs to none and stays. Settings ›
   General › Notifications off drops the subscription.

4. Dot's iPhone app (items 997 and 1353, `ios/`, installed from Xcode for now) is a native SwiftUI app over the daemon's own
   API: the chat list with the agents strip, a chat that opens from what the phone holds and catches up by the events
   after the last one, answers that stream in, the approvals and questions a run asks with Allow and Deny, one capsule
   over the composer (item 1390, as T3 Code's phone has it) whose segments are the run's status (working with its clock, waiting on you,
   waiting on N helpers, or N helpers keep working in the background once a message of yours released the run from its wait, item 1351),
   the chat's loops and the queue, each a button that opens its own sheet (the queue's has an up and a down arrow, Send now and Remove for each message,
   and Resume when a stop held it), never stacked and not drawn when there is nothing to say,
   a run inspector (plan, helpers, the chat's schedules and loops with switches), the Schedules list and the models; the
   web UI stays as it is and opens full screen from Settings › Open web UI. The second slice (item 1361) adds a new chat
   (the pencil of the list: a message starts a chat of its own on the model and reasoning level picked, as the web's home
   does since item 1350, `POST /api/runs`; a model that cannot answer is refused by name with a button that sends the
   message on the default model) and an agent's first message from the strip when it has no chat; the model and level of
   a chat in the inspector's sheet (**Model for this chat**, kept by the daemon as the web's composer keeps them,
   `PATCH /api/threads/{id}`, and sent with each message together with the chat's mode and its agent); older messages
   (reaching the top of a chat reads a page of older runs, `GET /api/runs?before=`, and keeps the reader's place); a
   tap on a step's line opens its arguments and result (clipped, and it says how much was left out); a hold on a
   message offers Copy, Select text, the seven reactions on what Dot said, and Edit from here on your own message
   (`POST /api/runs/{id}/rewind`, the files kept as they are; not while a run works); what is typed and not sent is kept
   for each chat, and for a new chat, across launches; the composer's microphone dictates on the iPhone (Apple's Speech
   framework, `requiresOnDeviceRecognition`: no audio leaves the phone; it asks for the speech and the microphone
   permissions when first pressed, and ends in a line, with a way to the app's page in Settings when a permission is
   off, whenever it cannot start); and the Live Activity, its rows and a notification's Open action open the chat of
   their run (`dotagent://run/<run id>`, or the alert's `#/runs/<id>` route). What it still leaves to the web UI is in
   `roadmap/1353.md`.

   **On a bad link (items 1370 and 1371)** the app draws what it holds first and never makes the owner wait for the
   network. The list, the agents, each chat (its newest runs and their last 400 events, its queue, schedules and
   reactions), the models and the schedules are kept on the phone, one file each, written whole or not at all, in a
   layout with a version, within 24 MB (the chats read longest ago go first); a file that does not read is dropped and
   read from Dot again. A chat that was open before opens in its first frame. What it asks Dot is asked together and
   tried again: a read gives up after 10 seconds, is tried three times with a growing wait and a little chance in it,
   and while the phone has no link at all it waits for the link rather than spending its tries; a second ask for what is
   already being read waits for that one, and leaving a chat cancels what it was reading. A chat that was away through
   a long run does not read the whole run: at 500 events or more it asks for the newest 400
   (`GET /api/runs/{id}/events?tail=400`; an older Dot answers with the run's first events and no
   `X-Dot-Events-Tail` header, and the app then reads the pages as before) and says that some steps are not shown. One
   thin line under the status bar says when the phone has no link (*Offline — showing saved chats · last updated 3 min
   ago*, or *Offline — 2 waiting to send*), when Dot cannot be reached for more than a moment (*Reconnecting…*) and when
   the phone's key was refused; it never covers what is read. **What is done with no link is kept and sent when it
   returns, once and in order**: a message shows at once under the chat as *Waiting to send* (with *Retry now* and
   *Edit*) and goes with the key Dot starts a message once by (`POST /api/threads/{id}/queue` sent again after an answer
   that was lost is the same message; a message that waited more than a day is left to the owner to send again, as
   Dot keeps a key for a day); an answer to an approval or a question goes when the link is back, and says so when the
   question had expired meanwhile or was answered on the Mac; a stop, a removal from the queue and a new order of it go
   the same way. A photo of a message is kept beside it until it is sent. After the app leaves the screen it keeps its
   streams for 20 seconds (a call that ends, a quick look at another app) and then closes them, with no timer left
   running; back in front, they open at once from the last event they read, and the list is read again. The system may
   also give it a moment to refresh while it is out of sight (a background refresh, a push that says there is something
   new; iOS decides, and it may never). After the list is read, the chats that moved on are read ahead, six at most,
   three on a cellular link or a hotspot, and none with Low Data Mode on. It adds what a page on iOS cannot have: the runs at work on the lock screen and in the Dynamic Island (a Live Activity). Its look (items 1380 to 1382, `ios/README.md`
   › The design) is iOS-native: near-black surfaces in the dark and a cool white in the light, following the system, one
   calm blue, text that follows Dynamic Type, and Liquid Glass on iOS 26 (a thin material before it) for the floating
   chrome only: the composer bar, the capsule over it, the agents' chips (a ring on each that turns while the
   agent works and is amber while it waits for you), the new-chat button and the Allow and Deny of an approval, which is
   the loudest card in a chat; message text and lists are never glass. What it still leaves to the web UI is in
   `roadmap/1353.md`. The
   app's notifications are its own, through APNs: Settings in the app opens the
   iPhone's Settings › Dot, where they are switched (item 1005; the web push switches do nothing in the app). The
   app asks once for the tailnet address (`https://<mac>.<tailnet>.ts.net`; it loads nothing else) and is signed in with
   a code as a browser is: the code of Settings › Phone typed in the app, which trades it for the phone's own key
   (`POST /api/phone/pair`, no token needed) and keeps it in its Keychain (or the web UI's page hands the key over, one
   `dot.paired` message, taken from the daemon's own page alone). It registers what
   Apple gives it with the key: `POST /api/phone/apns` (the device token and the environment; the token that would start a
   Live Activity by push is no longer sent, item 1096) and, for the Live Activity once opened, `POST /api/phone/apns/activity` (`DELETE` once it was swiped
   away, and when the app opens with none). Only a phone's own key registers
   (403 for the Mac's token). The daemon pushes through APNs with the key of `[apns]` (`key_path`, a `.p8` file outside
   Dot's home, never logged; `key_id`, `team_id`, `topic`), and pushes nothing through it while they are empty. An alert
   says the title Web Push says and a fixed line ("Open Dot to see it."), never the question, the draft or the result,
   since Apple reads what APNs carries; a message Dot wrote to you goes as "Dot has a message for you". An approval of a
   step of low or medium risk has Approve and Deny on the lock screen (item 1008), each after Face ID or the passcode,
   which the app sends as the app's page does (`POST /api/approvals/{id}/decision` with the phone's key, no note, no
   "don't ask again"); a step of high risk (money, a message or a post sent for you, a credential), a question, a
   secret and a handoff open Dot, where you see what is asked. An answer that does not reach the daemon is said in a
   notification of its own ("Dot did not get your answer"); one the daemon no longer waits for is let go. Another
   app's Share sheet has Dot (item 1009): a page's address, a text or up to four pictures (sent as JPEG, 2,048 px on the
   longer side at most) go to Dot as a new chat, with the words you add of what to do with it ("Look at this." when you
   add none), through `POST /api/attachments` and `POST /api/runs` with the phone's key; the share sheet reads the
   address the app was given (which a build with `DOT_HOST` held to that host) and its key, and sends nothing until you
   tap Send. A picture is made small without being opened whole (ImageIO), as a large photo opened whole took more than
   a share sheet may hold. The phone has one Live Activity (item 1093): on glass, "Dot" and how many
   runs are at work ("2 at work", "All done"), and a row for each run of yours (not a helper's, a schedule's, a
   watcher's or Dot's own), the runs at work first, then those that ended last, four at most: its chat's title (40
   characters at most), what it does now (item 1004: "Thinking", "Browsing the web", "Searching the web", "Running a
   command", "Reading files", "Writing files", "Using your Mac", "Using <app>" for a connected app, "Working with
   helpers", "Writing the answer"; never what the step was about: no address, file, command or words of the model) or,
   once it ended, the agent that worked on it, and its status in its colour (Working, Waiting, Done, Failed, Stopped).
   The Dynamic Island shows what the newest run at work does now, as the symbol of its tool (a globe browsing, a
   terminal running a command…), pulsing; an orange hand while a run waits for you, a check once none works (item
   1113). A row's status stays whole on its line; the title gives way. The app opens the activity itself when you
   start a run in it (a message, a
   queued message, a resume or a loop: the page posts `{type: "working"}`), unless one is up, and it comes without a
   notification (item 1096): the daemon starts none by push, as Apple has a push that starts one bring a notification,
   and a start is not worth one. A run started on the Mac, or by a schedule, opens no activity; you are notified when a
   run of yours ends or waits for you, as you allowed Dot's notifications in the iPhone's Settings. Once the app sent
   the activity's token every change goes to it, and an activity whose token came before its run started waits a
   minute for it (one opened by a request that started no run, a message queued behind a held queue, ends then); a step goes at most every four seconds, the latest kept, with APNs's priority that spares the battery; a start,
   "Waiting" while an approval waits, and an end go at once. An ended run stays a row 15 minutes, and once no row is
   left the activity ends and goes; a run that ended where no event told of it (closed as the daemon started, its
   command's process gone) is read as ended within a minute (item 1108). One you swipe away is forgotten once the app has told so (at once while it runs,
   else when it next opens: until then the changes go to the one that is gone), and the next run you start in the app
   opens a new one; one older than the eight hours iOS keeps one is forgotten; a daemon that starts again goes on
   showing the runs still at work on the one it finds, and ends it when none is. One with no change for four hours is
   marked stale. A token APNs calls gone (410,
   `Unregistered`, `BadDeviceToken`, `DeviceTokenNotForTopic`) is forgotten, and so are a phone's tokens when it is
   removed.

On a phone Dot works as an iPhone app does (item 1079): a menu (a chat's "…", a row's "…") is a sheet from the foot
of the screen, its choices in groups and Cancel at the foot; a finger's hold of a chat's row in the sidebar opens the
same sheet, as a right-click does with a mouse; a swipe from the screen's left edge does what the button at the top
left does (back, or the list of chats), and in the iPhone app it is the only swipe back: WebKit's own went back in the
history at the same time, to the chat opened before (item 1272; it takes a new build of the app, and in a Safari tab
Safari's own swipe goes back in the history, which a page cannot turn off, so on a phone's touch screen a link of the sidebar, a notification tapped, the toast's Open and the home after the open chat was deleted take the place of the page left in the history instead of adding one: what it leaves behind is nothing to go back to, and the swipe is Dot's, item 1321; a page opened by a link of a page keeps its place, and Back leads to it); a row of a list and a chat's row darken while pressed, with no grey box of the
browser, and nothing stays tinted after a tap (item 1086: every style of a pointer resting on something applies only
where a pointer can rest, `@media (hover: hover)`, which `tests/unit/test_touch_hover.py` holds), and a sheet takes
the focus itself, so that its first choice is not drawn ringed; a queued message moves a place up or down from its own two 44 px arrows (item 1348: a drag on a row took the swipe
that scrolls the chat, and is gone). Settings ›
General › Notifications reads again what the iPhone's Settings allow when you come back to Dot: before, a switch
turned off by iOS stayed off until the page was loaded again, which a Home Screen app cannot be, and it said to reload.

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
and the like are 13 px; Settings › Appearance's Interface size still scales them all, and code keeps the size chosen
for it. A phone's buttons and fields are 36 px tall and the small ones 32 (an icon button answers a finger's 44 px around it, and a button keeps its words, item 787), the heads of the steps and the rows of a
delegated run keep 40, and what sits in a line (a chip, a way to a page) 32; a link inside a line of text is no target
of its own. A switch's 32 by 18 px track (the same as on a Mac, item 1325) sits in a 48 px box (item 337); a tap anywhere on a setting's row turns its
switch, and a step's duration shows without a hover. An iPad and a Mac keep their
sizes. Where a page names a setting of the config file, it says what it is in words, the key after it in faint type.

Every screen was looked at on a phone, shot a screen at a time at 390 by 844 (item 606): what is tapped without being a
button is 32 px or more (the tabs of the details, the name of a file a run changed, the star of a model, a licence's
notice); a toast stands under the bar, across the page, and never over the composer, its send button or the stop button
of a run at work; a sentence with a button beside it (the token this browser keeps, Appearance's two choices that follow
the theme) gives the button a line of its own; a note the owner decides on (a forgotten one) has its buttons under its
words; the buttons of a version in Self-improvement wrap rather than reach past the edge of a phone 320 px wide; the
file's panel keeps the room for the file's name, and Open on the Mac is its icon; a file open over the whole screen
keeps its head (its name, Close and its views) under the status bar and the Dynamic Island (item 1006); Add and Cancel in a service's key form
have space between them; a ready team the owner has already says so under its words, which have the card's width; and
the name of a plugin is not cut by an ellipsis while there is room for it, which WebKit did to every name of two words.

On a phone the settings open on one sheet (item 995, the owner's ask of 2026-11-05 for one plain sheet; before, a list
of seven sections, items 330 and 845), which Settings at the foot of the sidebar leads to (item 1328; before, your initials at the top of the home): General,
Appearance, Rules, Memory, Vault and Snapshots, then the groups Agents (Agents, Models, Browser, Web search, Scheduled tasks,
Campaigns, Self-improvement; the sidebar has no Schedules icon, so its page is a row here), Connections (Connectors, Plugins,
Skills, AI services, Phone, Channels), Maintenance (System, Self-repair, Diagnostics) and About (Open source licences), and under them the first
steps while one is left (item 996: over the rows they came late and pushed a row from under a finger).
A row is one tab, its name over a line that says what it holds, with what it holds at its right (the theme on screen,
how many notes there are, how many items the vault holds; Models no model's name, item 852). A row opens its tab on its
own, the bar naming the tab, with no row of the section's tabs over it, and a card's name stands over the
card, as a section's title does on the redrawn tabs (item 576); a card that folds keeps its name on its ground. In a
Mac's window the sidebar is the settings' nav while they are open, the same rows by their names (items 562 and 999); see
[the daemon, the web UI and the service](daemon.md#the-daemon-the-web-ui-and-the-service). On a phone
Appearance draws the colour schemes as phones and puts the themes two a row, and its Interface size starts at a phone's 16 px
(item 331). A service's page of Settings › Connectors and an agent's editor name their cards the same way, a row of the
home's Today strip keeps its facts to one line that fades where it runs out of room, and Handoffs' empty page says what
brings messages there (item 332).

At a phone's width (720 px and under) Dot is laid out like a phone app (items 294 to 298). The sidebar is the
list of chats over the whole screen, the phone's home (item 991), and the same component as a Mac's, a finger's room
larger (item 1328, after the reference app's): the search at the top with three small buttons at its right (the agents'
menu, a new agent (item 1259: its face and its name, then its conversation) and a message to Dot), the chats as rows of three
lines (the agent's badge and name, the title, the last line; item 1327), the dock of **Working** and **Settled** under them
and, at the foot under the thumb, four small buttons: Settings, Connect apps (item 492), Watch the Mac and Models. The agents
are the menu behind the first button, not rows of faces (until item 1328, the agents you worked with last stood as rows of
44 px faces, item 1265): each its badge, its name and how many chats it has, to its conversation (its page while it has
none), then All agents, New agent and Campaigns. A conversation with an agent is a row among the days like any chat
(until item 1328 the agents' conversations left the days), and a search finds it there. A chat's bar is the Mac's (item 1329): 52 px, on the page's own ground, sticky, with a hairline under it only once the
conversation has scrolled; at its left the way back (items 338, 1079 and 1321: the rules of Back and the edge swipe are
unchanged), then the breadcrumb, the agent's badge and name, a slash and the chat's title (the model is in the panel's
Details); at its right the panel's switch, Dot's computer once the run has had a page and the helpers with their count. The
conversation begins under the bar, which no longer floats over it as three panes of glass (item 1119). A new chat and an
agent's page wear the same bar (item 1123). An agent's page
is its conversation (item 1272): it loads as a chat does, Dot's three dots in the middle, its face, name and box stand
in the middle of the room under the bar, and its way back is the list of chats, or the agents' page it was opened from. The line over the
composer is one bar (item 401): how long the run has worked (or that it waits for you), how its subagents stand
and how many messages wait in the queue at its left, each part opening what it tells of, and the way down at its right
end, in the same bar; it is a row of 40 px, a plain surface on a hairline and the card's corner, with no glass, no shadow
and no pill (items 352 and 360, drawn so since item 1335; a wider window has the same bar), as wide as its parts and the
way down and no wider, in the middle of the dock, not across all of it (item 427). While the task waits for its
subagents a line over it says **Waiting on 2 subagents**, with Details and Stop (item 1331): a pane on the bar's own
ground and hairline, in the dock under the conversation and never in it, so it stays where it is however far the chat is
scrolled up and no message shows through it (item 1347).
A list row (Campaigns, Scheduled tasks, agents, notes) is its face or switch, its title and its buttons on one line, and
what else it says (chips, text, times) under them across the row's whole width; a card's head with a line beside its
name puts the line under the name (item 436). The conversation keeps a chat's rhythm (item 401):
a turn after a turn keeps 8 px and the rows of what a run did 4, what you wrote sits in a box that holds its words in 8 px above and
below and 12 at the sides and is rounded 10 px, one of a line and a longer one alike, as on a Mac (items 1101, 1205 and
1324; what Dot says has no box, item 1335), a step's head is 32 px tall with a call's row 34 px from the next and no empty line under it (item 438), so a
phone shows more of what the run did without shrinking the prose. A chat is one screen (item 352): the bar
and the composer stay where they are and only the conversation scrolls, so a scroll, its bounce at either end and the
keyboard move nothing else. The conversation is that one scroller and it is as wide as the screen (item 1349): the page's
12 px at its sides and its 16 px under the bar, which scroll nothing, are the conversation's own padding now, so the
system's scroll bar stands at the screen's edge and a swipe that begins at the edge scrolls; the dock lets a touch through
where nothing of it is drawn, so the gaps beside the bar and the queue scroll the conversation too, and the way down's
invisible 4 px ring no longer covers the dock while it shows. The window never scrolls and does not stretch past its ends;
on the pages that scroll (the chats, Settings, Models, Memory) the window alone scrolls, with no box of the page beside it
(`tests/smoke/test_chat_one_scroller.py`). The composer stands at the conversation's foot on the page's own surface, a rounded box with a hairline edge (item 1330: the
pane of glass of items 374 and 401 and the quiet resting box of item 1120 are gone), with no band of the page's colour
behind it, and the conversation scrolls under the dock, its last line kept clear of the bar however tall the composer grows.
The words always take their whole line, and the line under them holds the "+", the model, the context meter, the microphone
and the send, each 36 px, from the start: nothing waits for a touch and no touch elsewhere puts the line away. The model's
pill stands for every choice of the run (item 1121): after the name it says the mode when one is named and the
reasoning level in small words, not the approvals, which the owner read as a part of the model's name (item 1163); a
long name gives way to an ellipsis and the level stays whole. A tap on it opens the chat's settings (item 1202), a
sheet from the foot of the screen with Close, the title and Done at its head: the default's row with Change, the
favourites, then a section per provider that opens and folds (the chosen model's open, the others saying how many
models they hold), each model with a tick and a star; under them the Options, Reasoning, Mode and Approvals as rows
that say their value and open in place, and How Dot works with its switches; the search of the models stays at the
sheet's foot. A pick keeps the sheet open; Done, Close, a tap on the dim above it or a drag of its head down puts it
away. The conversation scrolls up and down only
(item 360): a table or a block of code wider than the screen scrolls sideways in its own box, and a step's command or
tool name stays on its row's one line, cut by an ellipsis (items 512 and 513). A subagent's row, in the card of the group it belongs to, is two lines at this width, as on a wide window (items
1281 and 1331; item 401 made it one, which left the name the line and the goal three letters): its face, the job as its
title, then what it does now, each cut by an ellipsis rather than wrapped, with the word of its state over its time at the
right and the chevron; the way to its own page shows on the row once it is open, not as a second icon on every closed one. The
group's own head is one line. The details rise from the foot
as a sheet with a grabber, and a drag of its head down closes it; its Agents tab opens on the Lineage (item 1332: the
task and its subagents, each with how long it has worked and a dot for how it stands) and says of each agent its state,
its model and what it does now, or did last for one that ended (on a wide window too). The Screen tab with nothing yet says in the same empty panel as the
other tabs that Dot's computer is quiet and that the screenshots it takes while it works will appear there, with the
setting that turns it on. Dot's computer opens over the whole screen, with the page's address and who has control (Take
over), and goes back to the side panel when it closes. **Zoom** beside the address, or a tap on the page while it is
only watched (in a Mac's window too), opens the view in the full-screen viewer, live (item 1071): two fingers, a double tap or − and + zoom it,
to read a page's small print; the frames come at JPEG quality 80 (50 before), so the print holds when zoomed.
**Watch** in the sidebar's head shows the Mac's whole screen live, at any moment, with no task needed
([Watch](browser-and-computer-use.md#watch-the-macs-screen-live), item 1264); turn the phone sideways for a
larger picture.
**Use it here** holds the run and uses its page from the phone
(item 475): a tap clicks, a drag scrolls the page, a finger held still and then dragged drags on it, two fingers zoom
the picture, and **Keyboard** types into the page; the card of a browser step handed to you offers it too, and **Return
control** gives the run back and closes the view (item 491). A tab a tap opens is not the one the picture shows.

The button at the top left is the one way back (item 338): a chevron on the page's own ground, no circle around it
(item 373), in a 36 px box. On a chat's page, wherever it was opened from, it opens the list of chats in place, as the
sidebar comes over the page: the address stays the run's, and no page of chats stands between them (item 401). On a
delegated run's page it says Back and leads to the run that started it. On any other page opened by a link of another
page (a service from Connectors, a tab from the settings' list) it says Back and returns to that page; on a page opened
directly that has one above it (a settings tab, a service's page, a team's or a campaign's page, Handoffs) it
leads there; an agent's page is its conversation and opens the list of chats, as a chat's does (item 1272); on a page opened from the sidebar it is the way back to the list of chats. The browser's own Back and
Forward and a reload keep it as it was, and it is reckoned again when the phone turns. A chat that goes on at another of
its runs (a follow-up, the queue's next message, Continue, an answer, a rewind) stays the same page: it keeps its way to
the chats, and the browser's Back skips the run it went on from (item 345). The pages' own links back show in a wider
window only.

An app added to the home screen keeps storage of its own, apart from Safari's (iOS does so), so it has none of the token
that the signed-in address gave Safari, and its sign-in screen asks for a sign-in code instead (item 267; the same
screen opened on the Mac itself, at a loopback address, says to open the Dot app or to sign in from Terminal, and that
the code is for pairing a phone, which it takes too): Settings ›
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
extra checker (the typed judge) off, switch free mode on (item 922), add or switch on a rule that lets steps through
(allow, pre-approved), delete or
switch off a rule that asks, delete or switch off an allow rule that a pre-approved rule after it would stand in for
(an allow rule asks of a run that read outside content where a pre-approved one does not; item 981), or move any rule; what narrows (a rule that asks, the safe box on) it still does. What is
shown or connected on the Mac alone (the eye of the vault, 1Password, a sign-in of Google or Microsoft, the microphone's
pane of System Settings) answers the installation's token alone, never a phone's key, whatever address a phone's request
names. `yolo`, which asks nothing but a payment, a message or mail and a public post in a chat you opened (item 860), is
yours to use from the phone as from the Mac: a phone's key can start a run, a chat
or a campaign in `yolo`, make a schedule, a loop of a chat (`/loop`) or an agent kept in `yolo`, switch one on or let
one go on, and what such a run does is not held by these limits (it may do whatever your account may). Removing a phone
does not undo what it did: a phone that may have been used against you is removed, the installation's key made new as
well (below), and what it may have changed looked over (schedules, campaigns, agents, Settings). On a phone, Settings
› Phone says so and has no Show code, and Settings › Channels says where to pair a number. The paired phones' file is one of Dot's own files, which a run's commands and file
tools can neither read nor change in every mode but `yolo` ([Safety](safety.md)).

A phone that is lost, or that should no longer reach Dot: **Settings › Phone › Paired phones** lists each phone that
signed in with a code, with when it was paired and last used, and **Remove** signs it out at once (`DELETE
/api/phone/phones/{id}`): its key is refused from its next request, the streams it has open end, and it is back at the
sign-in screen, where a new code is needed. Its APNs device and the web push subscriptions it registered are deleted
with it (item 1311). A phone paired before item 670 holds the installation's own token, which
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
offers **Chat** (`/#/runs`, which read New chat before item 1279) and **Settings** (`/#/settings`). Since iOS 26, which opens what is added to the Home
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

Dot keeping a link to the phone from the Mac is another thing: see [Dot and your iPhone](phone-control.md).
