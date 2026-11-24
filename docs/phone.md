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
   over the composer (item 1390) whose segments are the run's status (working with its clock, waiting on you,
   waiting on N helpers, or N helpers keep working in the background once a message of yours released the run from its wait, item 1351,
   or once the run ended done and left them at work: "5 helpers", the line under the chat's title "5 helpers working" instead of
   "Done · 1 min ago", and the chat's row in the list, item 1466; while the chat is open the app asks for the list of helpers
   every 8 seconds as long as one is live, and keeps the last count, dimmed, with no link),
   the chat's loops and the queue, each a button that opens its own sheet (the queue's has an up and a down arrow, Send now and Remove for each message,
   and Resume when a stop held it), never stacked and not drawn when there is nothing to say,
   a run inspector (plan, helpers with the ones at work first, each group newest first with its count, item 1467, the chat's schedules and loops with switches), the Schedules list and the models; the
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

   **On a bad link (items 1370, 1371 and 1391)** the app draws what it holds first and never makes the owner wait for the
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
   three on a cellular link or a hotspot, and none with Low Data Mode on. A socket that died without a word (a path that died while the phone slept, a Wi-Fi that gave way to the cellular link) is not waited for: a connection that has answered nothing in 8 seconds, and an open stream that has said nothing for 35 seconds (Dot writes a keepalive every 15 seconds), are dropped and the stream goes on, from the last event read, on a new connection. It adds what a page on iOS cannot have: the runs at work on the lock screen and in the Dynamic Island (a Live Activity). Its look (items 1380 to 1382, `ios/README.md`
   › The design) is iOS-native: near-black surfaces in the dark and a cool white in the light, following the system, one
   calm blue, text that follows Dynamic Type, and Liquid Glass on iOS 26 (a thin material before it) for the floating
   chrome only: the composer bar, the capsule over it, the agents' chips (Dot wears its own face and every other agent the
   bot, one neutral disc whatever its id, item 1462; a ring on each that turns, once in four
   seconds, while the agent works and is amber while it waits for you), the compose and settings buttons of the title bar and the Allow once and Deny of an approval, which is
   the loudest card in a chat; message text and lists are never glass. Its icons are the web app's own, Lucide's
   (item 1461: `scripts/export_ios_icons.py` writes them from the web app's `icons.js`, so an icon the web app changes
   reaches the phone; the licence reads in Settings › About › Licences; the Live Activity and the share extension, which are
   other targets, still draw the system's symbols). What it still leaves to the web UI is in
   `roadmap/1353.md`. The
   app's notifications are its own, through APNs: Settings in the app opens the
   iPhone's Settings › Dot, where they are switched (item 1005; the web push switches do nothing in the app). The
   app asks once for the tailnet address (`https://<mac>.<tailnet>.ts.net`; it loads nothing else) and is signed in with
   a code as a browser is: the code of Settings › Phone typed in the app, which trades it for the phone's own key
   (`POST /api/phone/pair`, no token needed) and keeps it in its Keychain (or the web UI's page hands the key over, one
   `dot.paired` message, taken from the daemon's own page alone). It registers what
   Apple gives it with the key: `POST /api/phone/apns` (the device token and the environment; the token that starts a
   Live Activity by push goes by `POST /api/phone/live-start-token` since item 1425, not here) and, for the Live Activity once opened, `POST /api/phone/apns/activity` (`DELETE` once it was swiped
   away, and when the app opens with none). Only a phone's own key registers
   (403 for the Mac's token). The daemon pushes through APNs with the key of `[apns]` (`key_path`, a `.p8` file outside
   Dot's home, never logged; `key_id`, `team_id`, `topic`), and pushes nothing through it while they are empty. An alert
   says the title Web Push says and a fixed line ("Open Dot to see it."), never the question, the draft or the result,
   since Apple reads what APNs carries, and sets the app's badge to how many things wait on you (item 1422, the `count`
   of `GET /api/needs-you`: a number, nothing of what they are); a message Dot wrote to you goes as "Dot has a message for you". An approval of a
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
   notification (item 1096). A run started on the Mac, or by a schedule, opens no activity, but for a phone that gave
   its push-to-start token (items 1425 and 1445, iOS 17.2 and later, `POST /api/phone/live-start-token`, which the app sends as
   soon as iOS hands it over): the daemon starts the
   activity there by push, with the notification Apple requires of a start (a fixed title and line), when the phone shows
   none and the app is not in the foreground, at once for a command that ended in the background and for any other row
   of the activity (a run of yours, or the run that goes on in your chat when an agent's order came back or a run was
   resumed) once it has worked 30 seconds, and at most once in ten minutes for a phone (a schedule's run is no row of the
   activity, item 1096, so it starts none). You are notified when a
   run of yours ends or waits for you, as you allowed Dot's notifications in the iPhone's Settings. The daemon can keep
   the alert of a run's end from a phone whose app shows Dot (item 1420): the app says so with `POST /api/phone/activity`
   (in [the app's calls](#the-iphone-apps-calls) below), a lease that it renews every 25 seconds, and for as long as it
   holds the end of a run, of a schedule's run and of a campaign are not sent to that phone as alerts, while what asks
   something of you (an approval, a question, a secret, a hand-off), a draft, what Dot tells you and its own warnings
   are sent whatever it says, and so is every Live Activity update. The app sends the lease (item 1440), and agrees with it
   (item 1446): while it is active and shows a chat, an alert about that chat (a run of it or of its helpers, by the run
   its route names) is not drawn, whatever kind it is, since an approval or a question of that chat is its own card; an
   alert about another chat is a banner with its sound, and so is every alert while the app is inactive or shows the list. The app's pushes come in three kinds that each phone switches for itself (item 1421,
   `GET` and `PUT /api/phone/push-settings`): *needs you* (an approval, a question, a secret, a hand-off, and Dot's own
   failures you are to look at: a schedule that did not run or was switched off, learning that stopped, a restart after
   an unexpected stop), *ready* (a draft written for you to send, what Dot tells you on its own) and *done* (a run, a
   schedule's run or a campaign that ended); all three are on until the phone says otherwise, a kind that is off is not
   sent to that phone, and a schedule's own level of notices (Settings › Scheduled tasks) still decides whether the end
   of its run is told at all. Once the app sent
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
in the middle of the room under the bar, and its way back is the list of chats, or the agents' page it was opened from.
Over the composer there is one row and nothing stacked (item 1383, which ended the bar of item 401, the line that waited
on the subagents, the queue, the loops and the commands at work, five panes one upon the other at different widths): a
capsule of glass (a blurred translucent surface with a solid one where the browser draws no blur), its segments divided
by hairlines and each at least 44 px tall, in the middle of the dock, with the way down a round button beside it and
never a layer over it. Its segments are the run's status with its Stop (**Working · 36 s**, **Waiting for you**,
**Reconnecting…**), its plan, **2 helpers**, the loops (**every 10 min**, **2 loops**), **1 queued** and **1 running**,
each there only while it has something to say, and with none the capsule is not drawn at all. A row too narrow says
fewer words (the counts alone for the secondary segments, then the Stop without its word, then the time without
"Working") and at 320 px, with everything to say, takes a second line that keeps the status and the Stop in words, never
a scroll or a cut. A tap on a segment opens its detail as a sheet from the foot of the screen, one
at a time, which closes by a tap outside, by Escape, by its handle or by a swipe down: the queue's rows with their
arrows, the loops with when next and **Stop repeating**, the commands with their words wrapped and **Stop**, the helpers
with **Details** and **Stop**, the plan. What Dot asks, and the Continue of a task that was cut off, are cards of their
own directly over the capsule, never folded into it.
It respects reduced motion and its segments have focus rings, names and `aria-expanded`. A window wider than 720 px
keeps the bar of item 401 and the panes over the composer.
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
where nothing of it is drawn, so the gaps beside the capsule scroll the conversation too (item 1383), and the way down,
a round button beside the capsule, covers nothing of the dock while it shows. The window never scrolls and does not stretch past its ends;
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

**On a bad link: the web app (items 1520 to 1524).** The web app on the Home Screen does what the iPhone app does on a bad
link (above, items 1370, 1371 and 1391), with the same numbers and the same rules; its layout of files is its own. The
service worker stays what it is, the shell only, and caches nothing under `/api`: the page keeps its data itself.

- **What it keeps (item 1520).** The list of chats, the agents, the status and the other lists the shell draws, and for
  each chat it opened its newest 12 runs, their last 400 events and its queue, in IndexedDB (`dot-cache`, with a version
  in its layout). A chat is one record written in one transaction with the index of what is kept, so it is whole or not
  there; the cache holds 24 MB at most (the chats read longest ago go first, the chat on screen never), one chat never
  takes more than 2 MB (its oldest events go first), and a record that does not read, of another layout or cut short, is
  dropped and read from Dot again. Everything is dropped when the phone's key is forgotten and when the key or the
  address is not the one it was kept for, and no part of the key is kept (only a fingerprint of it, to tell whose cache
  it is). A page that cannot use IndexedDB (a private window, a store that is full) goes on as it was, reading from Dot.
- **What it draws first.** A chat read before is drawn from its copy at once, before Dot is asked anything, with its
  queue; the page then asks only for what changed: the stream goes on after the last event the copy holds, and the run's
  record, its questions and its lessons are read again. The lists answer from their copy while Dot cannot be reached, so
  a phone with no link opens on its chats instead of on "Dot cannot be reached"; a refusal of the daemon's (a key it
  does not accept, a run it no longer has) is never answered from a copy, and a run Dot no longer has is dropped from the
  cache. A copy is dated by when Dot last said that this is how the chat stands: one that was only drawn again never
  looks fresher than it is.
- **Reads that try again (item 1521).** On a phone's touch screen, once Dot has answered the page, one wrapper makes the
  reads (`js/api.js`, the rules in `js/retryrules.js`): a try gives up after 10 seconds, a read is tried three times
  with a wait that grows from 0.4 s to 3 s and has chance in it (half fixed, half random), within 20 seconds in all; a
  refusal (a 4xx but 408 and 429), an answer that cannot be read and a read that was cancelled are never asked again;
  while the phone has no link at all (`navigator.onLine`) or the page is out of view, a read waits for the link and the
  view instead of spending its tries; the same read asked while it is on its way is one request, each caller having an
  answer of its own to change; and a read ends when its page leaves (the chat's reads end when the chat is left). A
  list the phone holds a copy of is answered from it at once when there is no link, and after three seconds when Dot is
  slow to answer, Dot's own answer replacing it when it comes. A chat drawn from a copy that was at work when it was
  kept, and that missed 500 events or more of its run, reads the newest 400 (`GET /api/runs/{id}/events?tail=400`,
  header `X-Dot-Events-Tail`, item 1370; a daemon without it is ignored and the stream replays what was missed), says
  *Some steps are not shown*, and its stream goes on after the last event it read.
- **A stream that is never silent without being noticed (item 1522).** The page reads its streams with `fetch` and a
  stream reader, not with `EventSource`, which cannot see the comment lines that are Dot's `: open` and its `: keepalive`
  every 15 seconds, and so could tell a quiet stream from a dead socket by nothing. A small parser (`js/sseframes.js`)
  reads them: chunks cut anywhere, `\n`, `\r\n` and `\r` line ends, comments, `data` over several lines, ids, a frame cut by
  a drop (it is no event) and a frame whose data is not JSON (told of and skipped). A connection that has answered
  nothing in 8 seconds, and a stream that has said nothing, a keepalive included, for 35 seconds (it was 45), are dropped
  and made again; a drop is not shown as a failure, the note and the pill come after their usual quiet time. The stream
  goes on after the last event read, in the address (`after`) and in the standard header `Last-Event-ID`, so that no
  history is read again; it is made again at once when the page comes back into view and when the phone is back on the
  network, and otherwise after 0.5, 1, 2, 4, 8 and then every 15 seconds. A chat's stored steps, read once when it
  opens, keep the 45 seconds they had.

Dot keeping a link to the phone from the Mac is another thing: see [Dot and your iPhone](phone-control.md).

## The iPhone app's calls

What the native iPhone app asks of the daemon, as a contract the app can be written to (`ios/`). Every path is under
`/api` of the tailnet address the app was given (`https://<mac>.<tailnet>.ts.net`); bodies and answers are JSON.

**Auth.** `Authorization: Bearer phone.<id>.<secret>`: the phone's own key, from pairing with the code of Settings › Phone
(`POST /api/phone/pair`). A call that belongs to one phone (its lease, its switches, its tokens) answers **403** to the
Mac's own token, which belongs to none; `GET /api/needs-you`, `GET /api/away` and `GET` and `POST /api/away/read` are
read with either. With no valid token every call is a **401** (`a valid bearer token is required`), also with the key of a
phone that was removed. A request from another origin is a 403 and one to a host that is not Dot's a 421, before that.

**Errors.** Every error is `{"message": "...", "context": {...}}`. A body with a key that is not named, a value of another
shape or a query that is not valid is a **422** whose `context.errors` names each field (`type`, `loc`, `msg`; never the
value refused), and nothing was changed. A phone removed while its call was on its way is a **404** (`that phone is not
paired`) and nothing is kept for it.

**The calls at a glance** (the sections below have the bodies, the replies and what each changes):

| Call | Auth | Item | Answer |
| ---- | ---- | ---- | ------ |
| `POST /api/phone/activity` | the phone's key | 1420 | 200, the lease |
| `GET /api/phone/push-settings` | the phone's key | 1421 | 200, the three switches |
| `PUT /api/phone/push-settings` | the phone's key | 1421 | 200, the switches kept |
| `GET /api/needs-you` | any token | 1422, 1423 | 200, `{count, items}` |
| `POST /api/phone/live-start-token` | the phone's key | 1425 | 204 |
| `DELETE /api/phone/live-start-token` | the phone's key | 1425 | 204 |
| `GET /api/away?since=<seconds>` | any token | 1424 | 200, what Dot did on its own |
| `GET /api/away/read` | any token | 1356 | 200, `{read_at}` |
| `POST /api/away/read` | any token | 1356 | 200, `{read_at}` |

What changed in calls and replies that were there before: **every alert push carries `aps.badge`**, how many things
wait on you (item 1422; [below](#what-the-daemon-pushes-to-the-app)); `GET /api/away` lists more runs and says each run's
`source` (item 1424); and removing a phone (`DELETE /api/phone/phones/{id}`, the Mac's token alone) also drops its lease,
its switches and its push-to-start token. The registrations the app made before are as they were: `POST
/api/phone/apns` (`{"device_token", "start_token", "environment"}`: the device token in lowercase hex; `start_token`,
a key that must be there, `null` (a token in it is kept and never read: the token that starts a Live Activity goes by
`POST /api/phone/live-start-token`); and `"sandbox"` or `"production"`), `POST /api/phone/apns/activity` (`{"run_id":
"fleet", "token"}`) and `DELETE /api/phone/apns/activity` (with `?token=` to forget one activity's token), each 204.

### POST /api/phone/activity

The app says it is in the foreground, which chat it shows, or that it left (item 1420). The daemon keeps a *lease* for
the phone in memory: while it holds, the alerts of a run's end are not sent to the phone as APNs alerts (a good agent
app's phone keeps its ordinary alerts quiet in the foreground the same way); the app says it again every 25 seconds, and a lease it does not
renew (the phone lost its network, iOS suspended the app) ends by itself, which is the safe side: the alerts go.

Body:

```json
{"state": "foreground", "chat": "thr_mf3k2x1a_9c04d1", "ttl_s": 45}
```

- `state`: `foreground` or `background`. `background` ends the lease at once.
- `chat` (optional): the thread id on screen, 1 to 64 characters; `null` or left out when the app shows no chat (the
  list, Settings). The daemon only records it with the lease and says it back: while the lease holds the end of a run is
  not sent to the phone for this chat or for another, so it changes nothing yet; the app sends it so that a rule for the
  chat on screen can come without a new contract.
- `ttl_s` (optional): how many seconds the lease holds, more than 0; 45 when left out, and values over 90 are taken as 90.

Reply, 200:

```json
{"state": "foreground", "chat": "thr_mf3k2x1a_9c04d1", "ttl_s": 45.0, "expires_at": 1764000045.0}
```

`ttl_s` is the time as the daemon took it (capped), `expires_at` is when it ends, in seconds since the epoch. After
`background` the reply is `{"state": "background", "chat": null, "ttl_s": 0.0, "expires_at": null}`.

What it changes and what it does not: while a lease holds, the end of a run (done, failed, stopped), of a schedule's run
and of a campaign are not sent to that phone as alerts, for the chat on screen and for any other, as the app shows them
itself. Everything else is sent whatever the lease says: an approval, a question (also one a run left), a secret and a
hand-off, a draft written for you, what Dot tells you on its own and its own warnings (a schedule that did not run or was
switched off, learning that stopped, a restart). The Live Activity is not touched by the lease, nor are the other phones,
nor the web push of a browser. A phone that is removed loses its lease.

The iPhone app makes the lease (item 1440): it says `foreground` as its scene becomes active and again every 25 seconds while
the scene stays so, `foreground` with the new `chat` (the thread id) at once when the chat on its screen changes (`null`
on the list and in Settings), and `background` at once when the scene goes inactive (a system sheet, the app switcher) or
to the background, once. A report is one try with an 8-second wait and does not wait for a link, and one is on its way at
a time with only the newest behind it, so a poor link never piles requests up and `background` is never overtaken by an
older `foreground`. A report that does not get through is only logged, never shown: the lease ends by itself within 45
seconds, which is the safe side (the alerts go). An app that is not paired says nothing.

The same moment decides what the app draws (item 1446): while the app is active and shows a chat, an alert about that chat,
the run its `route` names being one of that chat's runs or its helpers', is handed to iOS with no banner and no sound and
is not added to the list of alerts (`willPresent` answers none); an alert about another chat is a banner with its sound
and its place in the list as before, an alert that names no run (a campaign, a schedule, Dot's own message) is drawn too,
and so is every alert while the app is inactive (a system sheet, the app switcher) or shows the list or Settings. What the
daemon holds back (the `done` kind's three events) and what the app does not draw (an alert about the chat in front of
the owner) are one quiet.

Errors: 403 for the Mac's token; 422 for a `state` that is neither `foreground` nor `background`, a `chat` that is empty
or over 64 characters, a `ttl_s` that is not a number above 0, or any other key (the lease is as it was); 404 for a phone
removed while the call was on its way.

### GET /api/phone/push-settings and PUT /api/phone/push-settings

Which of the three kinds of push the phone is told (item 1421). The switches are kept for the phone whose key asks
(a phone has its own; a removed phone loses them) and the daemon reads them for every alert it sends to that phone's
app through APNs; the Live Activity is not a kind of push and goes as before. A phone that never set them is told all
three.

- `needs_you`: something waits for your answer: an approval, a question (also one a run left), a secret, a hand-off;
  and Dot's own failures that you are to look at (a schedule that did not run or was switched off, learning that
  stopped, a restart after an unexpected stop), so that switching the ends of tasks off does not silence them.
- `ready`: something is ready for you to look over: a draft written for you to send, and what Dot tells you on its own
  (the morning brief, what a watcher found).
- `done`: something finished or failed: the end of a run (done, failed, stopped), of a schedule's run or of a campaign.
  A schedule's level of notices decides, as before, whether its run's end is told; this switch decides whether the
  phone hears what was told.

`GET` answers 200 with the switches as they stand:

```json
{"needs_you": true, "ready": true, "done": true}
```

`PUT` takes all three switches together (a missing one, one that is not `true` or `false`, or any other key is a 422)
and answers 200 with what it kept:

```json
{"needs_you": true, "ready": false, "done": false}
```

Errors: 403 for the Mac's token; 422 for a `PUT` that is not the three booleans (the switches are as they were); 404 for a
phone removed while its `PUT` was on its way. Web push to a browser has its own switch (Settings › General ›
Notifications) and is not changed by these.

The iPhone app has these switches in Settings, under **Notifications** (item 1441), each with one line of what it means
("Needs you": Dot waits on your answer, or something it does on its own needs a look; "Ready": a draft is ready to send, or
Dot has news for you; "Done": a task, a schedule or a campaign has finished, or failed). It reads them with a `GET` when
Settings opens and again when the phone's link comes back, and writes all three with a `PUT` when one is flipped: the
switch moves at once, a second flip while a write is on its way waits and goes after it with the latest of all three, and
a write the daemon does not keep (a refusal, or no answer) puts the switches back to what the daemon held and says so in a
plain line. With no answer to the read the phone shows the switches as it last knew them (kept in `awareness.json` of the
cache folder), dimmed, and they do not move: a switch that cannot be kept is no switch.

### GET /api/needs-you

Everything that waits on you, in one list, for the app's list and badge (item 1422): the approvals a run waits for, its
questions (`ask_user`), its requests for a secret (`ask_secret`) and its hand-offs, newest first, and what a run left for
you to answer later (a question it went on without, a step it kept for you). Any token reads it, a phone's key too. It
reads what the web UI's Activity lists as "waiting" and answers with it in the app's own shape; **the decision routes
are the same as before** (`POST /api/approvals/{id}/decision`, `POST /api/approvals/{id}/secret`).

Reply, 200:

```json
{
  "count": 4,
  "items": [
    {"id": "apr_mf3k2x1a_1c04d1", "kind": "secret", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
     "agent": "Dot", "title": "Dot asks for a secret", "since": 1764000400.0,
     "route": "dotagent://run/run_mf3k2w9z_a1b2c3"},
    {"id": "apr_mf3k2x11_7e8f9a", "kind": "handoff", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
     "agent": "Dot", "title": "Dot needs you on bank.example", "since": 1764000300.0,
     "route": "dotagent://run/run_mf3k2w9z_a1b2c3"},
    {"id": "apr_mf3k2x00_4e5f6a", "kind": "question", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
     "agent": "Scout", "title": "Which day should it ship?", "since": 1764000200.0,
     "route": "dotagent://run/run_mf3k2w9z_a1b2c3", "choices": ["Friday", "Monday"], "recommended": "Friday"},
    {"id": "apr_mf3k2wzz_7b8c9d", "kind": "approval", "run_id": "run_mf3k2w9z_a1b2c3", "thread_id": "thr_mf3k2w00_9d8e7f",
     "agent": "Dot", "title": "Run a command: make deploy", "since": 1764000100.0,
     "route": "dotagent://run/run_mf3k2w9z_a1b2c3"}
  ]
}
```

- `count`: how many things wait (the length of `items`): the number the app puts on its badge. Every alert the daemon
  sends sets the badge to it (`aps.badge`, a number); the app sets it again from `count` whenever it reads the list,
  since an answer given on the Mac sends no alert that would lower it.
- `id`: the approval's id, which the decision routes take. `run_id`: the run that asks, and `thread_id`: the chat to open
  (for a helper's run the chat of the run at the top). `agent`: who asks, the owner's agent that works on the run or
  `Dot`. `since`: when it was asked, in seconds since the epoch. `route`: the link that opens the run in the app
  (`dotagent://run/<run id>`; an alert's own route is `#/runs/<run id>`, as before).
- `kind`: `approval` (a step a run asks leave for), `question` (`ask_user`), `secret` (`ask_secret`) or `handoff` (a
  step only you can do, which the run waits for or kept for you: see Hand-offs below).
- `title`: what is asked, on one line of at most 120 characters (code points: the cut is made on those, with no mark that it was
  made), with secrets masked; for a `secret` a fixed line, never
  the card's words, and the list carries **no secret's value** (a request for one has none: it is told by its id and who
  asks); for a `handoff` a fixed line with the host of the page alone (below).
- `choices` (a `question` that offers options only): the options' labels, in the order the run gave them. `recommended`:
  the label of the option the run marked as its recommendation; both keys are left out when there is none. A question
  that takes several options (`multiple`) and the options' descriptions are on its card in the run's page.

A card of a run that no longer works here is not listed (the leftover of a daemon that stopped), one whose run is gone is
skipped, and a step kept for you whose time is over has lapsed and is no longer listed.

**Answering what the list holds** (the decision routes, which this item did not change; `id` is the item's `id`):

| `kind` | Answer |
| ------ | ------ |
| `approval` | `POST /api/approvals/{id}/decision` with `{"approve": true}` or `{"approve": false}`; `"note"` (text, optional), `"remember": true` (with a yes: do not ask again in this chat), `"by": "ui"` or `"cli"` are the other keys. 200, the approval as it is stored; 409 when it was answered already. |
| `question` | The same route; `"note"` carries the answer: the label of the option chosen (several joined with `"; "` when the question takes several), then what was written on a line of its own. `{"approve": false}` skips it. |
| `secret` | `POST /api/approvals/{id}/secret` with `{"value": "<the secret>"}` (1 to 8192 characters), which the daemon holds for the chat's commands for 24 hours and writes nowhere else; `{"approve": false}` on the decision route skips it (a yes there is a 422). |
| `handoff` | The decision route: `{"approve": true}` says you did it and the run goes on, `{"approve": false}` skips it. |

**Hand-offs** (item 1423). A step only you can do is a hand-off: the policy hands a credential (a password or a code the
run was not given, a login) and a payment to you in every mode that asks, and the run parks at it: it waits for your
answer and, unanswered in time, the step is refused or, with `[policy] unanswered = "park"`, kept for you while the run
goes on without it (`GET /api/needs-you` lists it either way). It is one of the
cards above, so it is **told as `needs_you` and pushed as such** ("Dot needs your help", the run's route and the fixed
line, nothing more), and it closes with the card: when you say you did it or skip it, when the run's wait ends, when the
step's time kept for you is over, or when the run ends. What the list says of it is the host of the page the step is on
and a fixed line, `Dot needs you on <host>` (`Dot needs your help` when the step names no web page), never the step's
own words, the address's user, path, query or fragment, what was typed, or anything the page showed; and **one hand-off
for a chat**: the newest (the ones kept before it wait behind it, and show once it is answered), counted once in `count`.
Answer it as before with `POST /api/approvals/{id}/decision`. What is **not** a hand-off here: a human check (a CAPTCHA,
"Press & Hold", Turnstile), which Dot passes itself as a standing instruction ([Captcha](captcha.md)), and a login page
that Dot meets without a step the policy hands over (it asks you with `ask_user`, a `question`, or with `ask_secret`, a
`secret`): the browser tools give the daemon no sure sign that a page is one only you can pass, so none is recorded.

**The iPhone app's inbox** (item 1442). The app reads this list when it starts, when it comes to the front or the link is
back, when the chat list is pulled down, when an alert arrives or a push wakes it, and when a notice of its stream says a
card was asked or answered, a question was left or a run ended; it draws the list from the last read at once (`awareness.json`
in the cache folder) and puts `count` on the app's icon after every read (`UNUserNotificationCenter.setBadgeCount`). While
anything waits, the chat list has one card at its top (the count on a badge, and what the newest asks) that opens the
inbox, newest first. Each row says who asks and in which chat, and what: an approval says what Dot wants to do (the line
taken apart as the chat's card takes it) with **Allow once** and **Deny**; a question shows its options as buttons, the
recommended one marked, or a field for words when it offers none; both answer through the outbox and the decision route
above, as the chat's card does. **Allow once is not offered for an approval whose line is 120 code points long**: the
daemon cuts a title there (it counts code points, so a line of combining marks or an emoji sequence is cut at 120 of them
and reads as fewer characters) and says nothing of it, so that line may be the start of a longer one, and the row says to
read all of it in the chat (Deny stays). **A secret is never answered on the phone**: its row says "Dot needs a secret: enter it
on your Mac" and has no field or button that takes a value. A hand-off and a kind the app does not know are answered in
the chat, which each row opens (`dotagent://run/<run id>`).

**The hand-off card** (item 1444). A chat whose run waits for a step only the owner can do in the browser (a `handoff` row of
this list) shows one calm card in its transcript: "Dot is waiting for you in the browser on `<host>`", a line that Dot
cannot go on until the step is done, and a button, **Open in Safari**, that opens the run's page of the web UI on the
address the app already holds (`https://<mac>/#/runs/<run id>`) in the system's browser, where **Take over** lets the
owner do the step from the phone ([Daemon](daemon.md)); the chat stays one tap away by the deep link
(`dotagent://run/<run id>`), and the inbox's row says the same words with the same button. The card says what the daemon's
fixed line says and nothing else: the host is shown only when it is plainly a host (letters, digits, `-`, `.`, `_`, `:`; no
space, path, `@` or query), so no credential, address part, page text or typed word can come to it through the line, and
without a host it says only that Dot is waiting. The step is not answered with a tap on the phone: the run goes on when
the owner hands the browser back or answers in the web UI.

### POST /api/phone/live-start-token and DELETE /api/phone/live-start-token

The token that starts the phone's Live Activity by push (item 1425; `Activity<DotFleetAttributes>.pushToStartTokenUpdates`,
iOS 17.2 and later). The app sends it as iOS gives it, and again whenever it changes, with the phone's own key; it need
not have registered its device (`POST /api/phone/apns`) first, and registering the device never erases it. The Mac's
token is refused (403).

Body of the `POST`, the token in lowercase hex (64 to 512 characters; any other key or shape is a 422):

```json
{"token": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718"}
```

Reply, 204, with no body. `DELETE` (204, no body) forgets the phone's token: the app says it has none (Live Activities
switched off). A token APNs calls gone, and a removed phone, are forgotten too. Errors: 403 for the Mac's token; 422 for
a token that is not lowercase hex of 64 to 512 characters or a body with another key; 404 for a phone removed while the
call was on its way (nothing is kept).

The iPhone app sends the token (item 1445) as `Activity<DotFleetAttributes>.pushToStartTokenUpdates` gives it, the one it
holds when it starts and each change after, once each (a token the daemon already has is not sent again, and one that is
not 64 to 512 characters of lowercase hex is not sent at all); it needs iOS 18, which is above the 17.2 it asks. The
device's registration still carries `start_token: null`. The app sends the `DELETE` when the phone's key is forgotten
(Settings › Forget this phone's key, or a changed address), before the key goes, and when Live Activities are switched off
for Dot in the iPhone's settings (switched on again, the token goes again); a `DELETE` that does not get through (no link)
leaves the token with the daemon until the phone is removed there, which forgets it too. The activity's own token, which
ActivityKit gives once the activity started, goes as it did (`POST /api/phone/apns/activity`).

What the daemon does with it: when a row of the Live Activity is at work (a command that ended in the background at once,
any other row after 30 seconds of work: a run of the owner's, or the run that goes on in their chat when an agent's order
came back or a run was resumed), the phone has no activity registered (`POST
/api/phone/apns/activity`), its lease (`POST /api/phone/activity`) is not foreground, and no start was sent to it in the
last ten minutes for the runs at work, it posts this to APNs, to the phone's token, with `apns-push-type: liveactivity`,
`apns-topic: <bundle id>.push-type.liveactivity` and `apns-priority: 10`:

```json
{
  "aps": {
    "timestamp": 1764000000,
    "event": "start",
    "content-state": {"active": 1, "rows": [{"runId": "run_mf3k2w9z_a1b2c3", "title": "Deploy the site", "agent": "Dot",
      "status": "working", "kind": "thinking", "line": "Thinking", "startedAt": 1763999970.0}]},
    "attributes-type": "DotFleetAttributes",
    "attributes": {"fleetId": "fleet"},
    "input-push-token": 1,
    "alert": {"title": "Dot is working", "body": "Open Dot to see it."},
    "stale-date": 1764014400
  }
}
```

`content-state` and `attributes` are `DotFleetAttributes.ContentState` and `DotFleetAttributes` of `ios/Shared`, exactly as
the updates of an open activity carry them; the alert is the lock-screen text of the start and is fixed: no run's
title, command, address or answer goes in it (the chat's short title is in the first row, as in every update). The app
that gets it starts the activity, is woken, and sends the activity's own token (`POST /api/phone/apns/activity`), after
which every change goes to that token as before. This follows Apple's "Starting and updating Live Activities with
ActivityKit push notifications": the push-to-start token comes from `pushToStartTokenUpdates` and goes to the server; a
start is `event: start` with `attributes-type`, `attributes`, `content-state`, `timestamp` and an `alert`, which is
required, and, for iOS 18 and later, `input-push-token: 1` "to start a Live Activity and receive a new push token" (the
daemon needs the started activity's own token, which the app sends it; iOS 17.2 does not read the key; not tried on a
phone); the headers are `apns-push-type: liveactivity`, `apns-topic: <bundle id>.push-type.liveactivity` and
`apns-priority` 5 or 10, 10 counting toward the hourly budget, so a start, rare by its rate, goes at 10. A phone whose
token APNs refuses as gone loses the token and not its device; the app gives a new one when iOS changes it.

### GET /api/away, GET /api/away/read and POST /api/away/read

What Dot did on its own while you were away (item 1424), and the moment you last looked, which Dot keeps for every device
(item 1356: the iPhone, the Mac and a browser agree on what is news). Any token reads and moves them, a phone's key too.

`GET /api/away?since=<seconds since the epoch>`. `since` is required and a number of 0 or more (a missing or negative one
is a 422); the daemon takes it within the last seven days and never after now. Reply, 200 (`until` is now):

```json
{
  "since": 1764000000.0, "until": 1764003600.0,
  "done": 3, "failed": 1, "cancelled": 0, "working": 1, "campaign_sessions": 0,
  "runs": [
    {"run_id": "run_mf3k2x1a_1c04d1", "title": "Deploy the site", "source": "background", "campaign": false,
     "agent_id": null, "status": "done", "steps": 4, "created_at": 1764000100.0, "finished_at": 1764000160.0,
     "answer": "The deploy finished."}
  ],
  "decisions": {"reviewer_approved": 0, "reviewer_denied": 0, "yolo_approved": 2, "yolo_denied": 0},
  "decision_runs": [{"run_id": "run_mf3k2x1a_1c04d1", "title": "Deploy the site", "reviewer": 0, "yolo": 2, "denied": 0}],
  "drafts": 1
}
```

- The runs are those of every source Dot starts by itself and nobody watches (a schedule or a campaign, a watcher's run,
  Dot's own initiative, a reply to a mail), an agent's work (`team`: a run sent it, so it has a parent; not one with a run
  of the owner's above it, which they wait for with the work in it) and a command that ended in the background
  (`background`), and what ran in an agent's own chat under a source that is not the owner's (`ui`, `cli`, a review, an
  eval, an agent's welcome and a run resumed after one the owner started are theirs: they wait for them, and are not
  listed). Dot keeps no record of who was looking while a run worked, so a run the owner started and walked away from is
  not listed either; the chat's unread dot says it ended.
- `title` is the name of the campaign or the schedule, else of the chat (for a reply, an agent's work or a command in the
  background), else the first line of the goal; `source` says what started the run; `status` is `done`, `failed`,
  `cancelled`, or a state of a run that works (`queued`, `running`, `blocked`, `ending`); `answer` is the first line of its
  answer with its secrets masked (200 characters at most). The counts are of every such run since the moment, `runs` is
  the newest fifty, and the view carries no summary and no arguments of an approval.
- `decisions` and `decision_runs` count the approvals the review model and `yolo` answered for you in those runs and
  their helpers (the ten runs with the most); `drafts` is how many drafts they wrote that still wait for you.

`GET /api/away/read` answers 200 `{"read_at": 1764003000.0}` (`null` before any device marked it). `POST
/api/away/read` takes `{"until": 1764003600.0}` (the `until` of the card that was shown: a number of 0 or more, no other
key, or a 422) and answers 200 with the same shape, where the mark stands now: it never moves back and never past now. The
first visit from any device marks it and shows nothing, so the card never pours out history.

**The iPhone app's card** (item 1443). The app reads the mark when it starts, comes to the front or the link is back, and
when the chat list is pulled down. No device has set it yet: the app marks the moment (`POST` with the phone's now) and
shows nothing, so the card never pours out history. Otherwise it reads `GET /api/away?since=` against the mark and, when
Dot did anything in that time, shows one flat card at the top of the chat list: how many things Dot did on its own and how
they ended, the three newest (each a row that opens its chat) and how many more there are, and, when it did, how many
approvals Dot answered for you. Putting the card away marks the `until` of the card that was shown (`POST
/api/away/read`), so that the Mac and every phone agree on what is news; the card goes at once, and a mark that does not
get through (no link, no answer) is kept in `awareness.json` and sent first by the next read, so a card put away stays away.
The card is drawn from the last read, kept in the same file, before the network answers.

### What the daemon pushes to the app

Pushes go through APNs to every phone that registered a device (`POST /api/phone/apns`), signed with the key of `[apns]`,
as `POST https://api.push.apple.com/3/device/<device token>` (`api.sandbox.push.apple.com` for a phone that said
`"sandbox"`); nothing is pushed while `[apns]` is empty. Headers: `apns-topic: <bundle id>` and `apns-push-type: alert` for
an alert, `apns-topic: <bundle id>.push-type.liveactivity` and `apns-push-type: liveactivity` for the Live Activity, and
`apns-priority: 10`, or `5` for a step of a run at work.

**What a payload may carry.** Apple reads what APNs carries, so a push holds a title that is one of Dot's fixed lines, the
fixed line "Open Dot to see it.", a route, an id and a number, and nothing a person did or was asked: **never a command, an
address, page text, a file name, a credential, a question or an answer.** What a thing asks is read in the app, over the
phone's own key, after Face ID (`GET /api/needs-you`, the run's page). The Live Activity alone holds the chat's title (40
characters at most), the agent's name and a fixed word for what the run does now ("Thinking", "Browsing the web").

An **alert** (`alert_payload`):

```json
{
  "aps": {"alert": {"title": "A task is done", "body": "Open Dot to see it."}, "sound": "default", "badge": 2},
  "route": "#/runs/run_mf3k2w9z_a1b2c3"
}
```

- `aps.badge` (item 1422) is how many things wait on you at that moment, the `count` of `GET /api/needs-you`: a number,
  nothing of what they are. It is left out when the daemon cannot count: an alert is not held back for its badge. Set the
  badge again from `count` whenever the app reads the list, since an answer given on the Mac sends no alert that would
  lower it.
- `title` is one of: "Dot asks for approval", "Dot has a question", "Dot needs your help" (a hand-off, item 1423), "A task
  is done", "A task failed", "A task was stopped", "A task has ended", "A campaign is achieved", "A campaign stopped", "Dot drafted an email",
  "Dot drafted a message", "A schedule did not run", "A schedule was switched off", "Dot's learning has stopped", "Dot
  restarted after an unexpected stop" and, for a message Dot wrote to you, "Dot has a message for you" (never its own
  words). `route` is a route of Dot's own pages that the tap opens, always starting `#/`: `#/runs/<run id>`,
  `#/campaigns/<id>`, `#/settings/schedules`, `#/settings/memory` or `#/runs`.
- An **approval** of a step of low or medium risk adds `"category": "DOT_APPROVAL"` to `aps` and `"approval_id":
  "<approval id>"` next to `route`: the app's notification offers Approve and Deny, each after Face ID, and answers with
  `POST /api/approvals/{id}/decision`. A step of high risk, a question, a secret and a hand-off are alerts without the
  category: they open the app.

The **Live Activity** (`DotFleetAttributes` of `ios/Shared`) is told with `live_update_payload` (`"event": "update"`,
`content-state`, `stale-date`), ended with `live_end_payload` (`"event": "end"`, the last `content-state`,
`dismissal-date`), and started, on a phone that gave its push-to-start token, with the payload under `POST
/api/phone/live-start-token` above (`"event": "start"`, `attributes-type`, `attributes`, `input-push-token`, an `alert` of
the fixed title "Dot is working" and the fixed line). A change goes at most every four seconds, the latest kept, at
priority 5; a start, a wait and an end go at once at priority 10.

**Which pushes a phone is told.** Each event is one of three kinds, switched by `PUT /api/phone/push-settings`: `needs_you`
(`approval_requested`, `question_left`, a schedule that did not run or was switched off, learning that stopped, a restart
after an unexpected stop), `ready` (a draft written for you, what Dot tells you on its own) and `done` (the end of a run,
of a schedule's run at its schedule's level of notices, of a campaign). A kind that is off is not sent to that phone. While
the phone's lease (`POST /api/phone/activity`) holds, the `done` kind's three events are not sent to it as alerts; nothing
else is held back by it. A phone that is removed is sent nothing.
