# Browser and computer use

## Browser use

Dot works in Chrome in one of two ways, set by `[browser] mode`: in a Chrome of its own (`own`, the default) or in new
tabs of the Chrome you already have open (`attach`, see below).

**`own`** starts a Chrome of Dot's own on the profile in use (Default, `<home>/browser-profile`, unless Settings ›
Browser chose another; see Profiles below). It cannot drive your daily profile that way: since version 136 Chrome
refuses remote debugging on its default data directory, and it locks a profile while it runs. There are two ways to open
the sites Dot should use with your accounts:

- `dotx browser login [url]` opens Dot's profile as a normal Chrome window. Sign in, then quit Chrome (Cmd+Q); the
  cookies stay. Settings › Browser does the same with "Sign in…" (`POST /api/browser/sign-in`, item 344): Dot's own
  Chrome, when it is open, is closed first once you say so (refused with 409 while runs are active), the card says the
  window is open until you quit it, and runs cannot use Dot's Chrome meanwhile.
- `dotx browser import-chrome [--profile "Profile 1"] [--replace]` copies the cookies and the local storage of a profile
  of your Chrome into Dot's profile, so the accounts that are open in your Chrome are open for Dot too. Quit Chrome
  first. If the Dot service has its browser open, stop it for the import (`dotx service uninstall`, import,
  `dotx service install`). The import holds Dot's profile while it copies and swaps the files in (item 554): a browser
  call of the service that would start Dot's Chrome on it meanwhile fails at once, saying an import is under way, and
  an import is refused while Dot's Chrome is starting on the profile. Your Chrome profile is only read and nothing is
  decrypted; the cookie values stay encrypted
  with the key that Chrome keeps in the Keychain, which Chrome itself reads when Dot's browser starts (macOS may ask
  once; choose Always Allow). The command ends by counting the cookies Chrome can read, and says when it reads fewer
  than it was given. The copy is a snapshot: a sign-in that expires or rotates needs a new import, and a site that keeps
  its sign-in in IndexedDB asks again. The import is all or nothing (item 529): a failure puts back what Dot's profile
  held, and leaves no copy of your Chrome's local storage behind; when it cannot put something right or cannot remove
  that copy, it says which file. The cookies of your Chrome that have no expiry are kept
  too, as in Dot's own profile (item 453). `--replace` is asked for only when Dot's profile holds a cookie, and the
  Incognito profile is refused, as Dot's Chrome empties it as it starts. When the Keychain refused Chrome, Chrome has
  dropped the cookies it could not read: allow it and import again with `--replace`.

Cookies are encrypted with a key, and a profile cannot be read with another key than the one it was written with. A
profile that Dot made uses Playwright's mock keychain in the sign-in window and in the automated sessions alike, so no
Keychain item is involved; the profile of an import uses the system keychain in both (`dotx doctor` shows which). A
sign-in made with an older version of `dotx browser login`, which used the real key, has to be made again.

Dot's own Chrome loads the extensions installed in its profile: Playwright starts Chrome with `--disable-extensions`,
which Dot leaves out (item 264). Install one from the Chrome Web Store in the window of `dotx browser login`; it stays
in the profile and loads at every start of Dot's Chrome.

**`attach`** works in new tabs of the Chrome you already have open, signed in as you are, and starts no Chrome of its
own. It needs Chrome 144 or later and its switch "Allow remote debugging for this browser instance" at
`chrome://inspect/#remote-debugging`, which only you can turn on.

```bash
dotx browser attach     # check Chrome, work in one blank test tab, save mode = "attach"
dotx service restart    # restart the service, so that it uses your Chrome (a run in yolo asks for --when-idle)
dotx browser detach     # go back to Dot's own Chrome (restart the service again)
```

While `dotx browser attach` runs, Chrome asks whether to allow the connection: click Allow within `attach_timeout_s` (60
seconds). The check opens one blank tab, works in it and closes it again; your other tabs are not touched. The sign-ins
Dot works with are the ones of your Chrome, so nothing is copied and nothing goes stale. What to expect:

- Dot connects once and keeps the connection for later runs, which need no click. Chrome asks again whenever Dot makes
  a new connection: after a restart of the service, or after Chrome itself was closed or restarted. Then the next
  browser call connects again by itself, a notification says "Chrome asks to allow Dot", and a step that waits for
  the click, or that was under way when Chrome went, says in one line that Chrome was closed or restarted and that
  Allow is to be clicked (item 341). Settings › Browser reads how Chrome stands every 3 seconds while it is open:
  connected, waiting for Allow, Chrome closed, remote debugging off, or Chrome restarted, each with what to do. A
  schedule that fires with nobody there waits `attach_timeout_s` for the click and then fails.
- A tab that Dot opens is opened in a window of its own, made behind the window you work in, also when Chrome is the
  app in front (item 340): the app in front stays in front and your windows and tabs are not touched. Its page runs
  at full speed, as any tab of Dot's does: Dot emulates focus for it, without which Chrome draws no frame of a tab in
  the background. The window closes with the tab. Making a window takes about 30 ms more than a tab, once per chat
  and for each quiet read. Dot never minimizes it (a minimized window is throttled); a window covered by others runs
  at full speed.
- Downloads go to the download folder of Chrome, not to Dot's `downloads/`.
- Dot closes the tabs it opened when they have been idle for `tab_idle_s`. If Dot itself crashes they stay open.
- Calls that arrive while Dot waits for the click queue behind it, and each may in turn wait up to `attach_timeout_s` if
  you do not click.
- Only the stable Google Chrome on its default data directory is supported. `channel`, `headless` and `raise_window`
  apply to `own` only.

The mode can also switch without a restart: Settings › Browser in the web UI, or `POST /api/browser/mode` with
`{"mode": "own"}` or `{"mode": "attach"}`. The live switch runs the same one-blank-tab proof; it is refused with 409
while runs are active, and a failed proof leaves the session in the Chrome it had (422). When your Chrome is closed,
the live switch opens it first, as a click on its icon does, and waits up to 30 seconds for it to start and 10 more for
it to listen; with remote debugging off, the switch then says how to turn it on (item 261). A session a campaign had
made for the mode that is switched to (`browser = "own"` or `"attach"` on the campaign) is closed first, so that its
Chrome does not keep the profile the switched session starts on (item 554). While the switch is under
way, Settings says what it waits for, and after a failed one it shows how Chrome stands. Runs, campaigns and
`dotx browser attach` never open Chrome; `dotx browser attach` and `dotx browser detach` only write the mode, which the
service takes up on its next start. The same tab's switch "Bring
Chrome to the front while working" is `POST /api/browser/raise` with `{"raise_window": true}` or `false`: it takes
effect at the next browser call, Chrome is not restarted, and the value is written to `config.toml` the same way.

`[browser] debug_port` (0, none, by default; else from 1024 to 65535) is a port of this Mac on which Dot's own Chrome
also takes DevTools connections, besides Playwright's pipe (item 265): Chrome listens on `127.0.0.1:<port>`, and a run
whose browser is Dot's own and that has a tool to run scripts (`shell`, `terminal_send`) is told the address in its
instructions, so a script it writes can drive Chrome with the Chrome DevTools Protocol in tabs of its own once one of
the run's browser calls has started Chrome. The run is told to connect with Playwright's `connect_over_cdp` and
`no_defaults=True` (`noDefaults` in Node), which leaves Dot's downloads as they are, or with Puppeteer's `connect` and
`defaultViewport: null`. Dot's Chrome does not start while another program holds the port, with an error that names
the port; a script reaches whatever listens on the port, which is Dot's Chrome while it runs. Settings › Browser sets it
(`POST /api/browser/debug-port`, refused while runs are active and when the port is taken): every browser session is
checked before any changes, Dot's Chrome, open on another port, is closed and starts on the new one with the next call
that needs it, and the value is written to `config.toml`. The port asks for no token: while Dot's Chrome runs, any
program on this Mac can connect to it and drive Chrome with the sign-ins of Dot's profile.


Settings › Browser has these rows (item 354), in sections each named over its card (items 378 and 564): where it
browses, the profiles of Dot's own Chrome, how pages look, while a task works, how to let Dot join your Chrome and,
under **Advanced** (shut until it is opened, item 625), how Chrome stands and the DevTools port; what Dot says of the moment (the sign-in window, the wait for Allow, a refusal) stands over
them while it holds, and **Look again** is the button of the page's head. **Tasks may use the browser** (`[browser]
enabled`, on by default; `POST /api/browser/enabled` with `{"enabled": false}`) keeps the browser tools from every run
that starts after it, an agent's and OpenCode's agent's too; a run at work keeps the tools it started with, and computer
use still sees the screen. **Size of a page**, **Appearance of pages** and **Zoom of pages** (item 358) are `POST
/api/browser/look` with `fit_window`, `viewport_width`, `viewport_height`, `appearance` and `zoom`, refused with 409
while runs are active and kept in `config.toml`. A page of Dot's own Chrome fills its window (`fit_window = true`; the
model is told the size the page has) or has the size given (1280 × 860 by default), and pages are told to prefer the
Mac's colour scheme (`appearance = "system"`, the default), light or dark. Dot's own Chrome draws pages at `zoom`
percent of their size (100 by default): it is Chrome's default zoom, written into the preferences of the profile in use
before Chrome starts on it, so a page of 1280 × 860 at 125 has the room of 1024 × 688; the model is told that room, and
its screenshots are drawn at it, so a pixel of one is a pixel for `click_at`. A site zoomed by hand with ⌘+ or ⌘− in
Dot's own Chrome keeps its own zoom. Dot's own Chrome is let go of and starts with them at the next call that needs it;
the tabs Dot opens in your Chrome are told the colour scheme one by one, and keep the size of its window and the zoom of
each site. Before, Dot's own Chrome told every page Playwright's default, light. **Show Dot's computer when a task opens
the browser** (kept in the browser you read Dot in, on by default) opens the details panel on its Screen tab when a run
at work first uses its browser, beside the chat on a wide window, without making it the panel or the tab you chose; its
switch hides it, and a phone keeps the button of its bar. Dot has no row for where links open (a link in Dot's chats
opens in the browser you read Dot in, and Sign in… covers signing in for Dot).


**Profiles** (item 355): Dot's own Chrome works on one profile at a time, each with its own cookies and
sign-ins. **Default** is the profile Dot always had (`browser-profile` in its home); **Incognito** (`browser-incognito`)
is emptied before Chrome starts on it and after it closes, so it keeps nothing; **Add profile** asks the name of another
in a dialog (item 615) and makes it
(`browser-profiles/<name>`, up to 24, a name of up to 48 letters, digits, spaces, `-` and `_`). **Use** makes runs work
on a profile from Chrome's next start (`[browser] profile`, `POST /api/browser/profile`, refused while runs are active);
**Clear** signs every site out of it (item 532): it deletes the cookies, what the sites stored (local and session
storage, IndexedDB, service workers and their caches), the logins and autofill Chrome saved, and the caches, and keeps
its history, settings and extensions (`POST /api/browser/profiles/<name>/clear`; Dot's own Chrome is let go of when it
works on it); **Delete** removes an added profile that is not in use (`DELETE
/api/browser/profiles/<name>`). A profile that a Chrome window has open (Sign in… opens one) is not changed. Sign in…,
`dotx browser login`, `dotx browser import-chrome` and `dotx doctor` work on the profile in use. Every profile is
guarded as Default is: a run neither reads nor changes it. In My Chrome mode the profiles are your Chrome's own.

A profile keeps what a real browser keeps (item 453): its history, its cookies with an expiry, and its session
cookies too, the ones a site gives no expiry and many keep a sign-in in. Chrome writes those into the profile while it
runs and deletes them as it starts again, unless its last run crashed; so before Dot's own Chrome, Sign in… or `dotx
browser login` start on a profile, Dot writes into its preferences that the last run crashed (`profile.exit_type`),
and Chrome starts with `--hide-crash-restore-bubble`: it gives the session cookies back and opens none of the pages of
that run. Incognito keeps nothing, as before. **Clear** still deletes them all.

Both modes share what follows.

- The page is observed as an accessibility snapshot in which every element has a ref (`e12`). It covers the viewport
  plus `viewport_margin_px`, and `browser_find`, `browser_links` and `browser_read_text` reach what is outside.
- A collapsed dropdown lists its options on its own line, for example
  `combobox "Shipping" [ref=e3]: options "Standard (3-5 days)" [selected], "Express (next day)"`. `select` takes the
  label or the value of one of them, written as listed; a choice that is not there is refused at once with the options
  that are, the closest first, instead of waiting for the timeout. An option that a script adds within a second of the
  call is still found. A very long list is cut with the number of options left out and the current choice.
- `browser_act` runs up to `max_batch_steps` steps in one call (5 by default; a longer batch is cut to the first ones
  and the result says how many remain, so the call is not lost): `click`, `double_click`, `right_click`, `hover`,
  `fill`, `type`, `insert`, `select`, `press`, `check`, `uncheck`, `scroll_into_view`, `drag` and `click_at`. After a
  step that can open a tab or a dialog (a click, `select`, `press`, `check`, `drag`) Dot lets the page take one turn of
  its event loop, by which Chrome has told of a dialog, a file chooser, a navigation or a tab the step set off, and
  waits for such a tab until it is the run's, at most a second (item 342): a tab whose page answers later stops the
  batch with a line that it is opening. Then Dot waits until the page has settled (no DOM change for
  `settle_quiet_ms`, 60 ms, at most `settle_max_ms`) and reports popups, dialogs and downloads that appeared. The result is the part of the page that changed, with the refs of
  the last snapshot still valid; a page that did not change comes back as one line that says so. A tab that closes under
  a batch (a sign-in callback that did its job, a tab the user closed) stops the batch without a failed step: the result
  says the page closed it and shows the tab that is now current. A popup that opens and closes itself before the model
  could see it is a note on the page that opened it. The page a batch leaves is a stranger's to write, so the run that
  reads it is marked untrusted, as `browser_upload` and `browser_fill_login` mark it by the page they leave (item 561;
  see [Safety](safety.md#safety)). A batch carries one payment step at most (a click on a control that asks to pay, by
  its words or by the typed judge's reading): two payments in one call would pass for the larger of their amounts under
  the caps of `[policy.spend]`, so the call is refused and the steps are sent one call at a time, in every mode.
- `fill` replaces a form field's value, `type` presses a key per character (autocomplete sees them), `insert` puts the
  text in at once at the caret without key events (long text, post composers, code editors) and `drag` moves an
  element onto the element `to_ref` (HTML5 drag and drop or a library driven by the mouse). `insert` goes only into a
  text field or an editor; at any other element it fails, so the text never lands in whatever field has the focus.
  `type` and `insert` need a text that is not empty. A drop onto a password, card or one-time-code field is refused
  like typing into it, and a drag that fails releases the mouse button.
- In a rich editor (a contenteditable area such as X's composer) `fill`, `type` and `insert` give the editor the focus
  when it does not have it and wait until it has settled, check the field that has the focus for a secret, write, and
  fail when the editor's text did not change, instead of reporting done; a `fill` that leaves the editor holding its
  text, such as the text it held already, is done. A write that makes the page open another one, such as Enter in a
  search box, is done.
- `browser_eval` runs JavaScript in the page of the run's own tab, in either mode, and gives back its value as JSON: an
  expression (`document.title`), a function, or a promise it awaits (`timeout_s`, 30 seconds by default, at most 600).
  It reads what the snapshot shortens (a whole list, a table, the data behind a page) and does in one call what would
  take many steps. A script that throws fails the call with its message; one still running when the wait ends goes on in
  the page, and the call says so. The page is settled after every script as after a step, so a script that leads the tab
  elsewhere lets it land, also when the page answers after the script's value came back (item 270), and says where, and
  the URL rules hold after it as after a step: a forbidden address resets the tab and fails the call. The value is
  fenced as the page's text and makes the run untrusted. As an action it is a click of medium risk on the tab's page:
  the aggressive and balanced presets allow it, the cautious preset asks, a research run does not have it and a `yolo`
  run does it without asking. What the script does inside the page is not checked or paced action by action, as
  `browser_act`'s steps are; its words are read for a secret, as a command line's are: in a run that read outside
  content, a script that carries a key or a secret Dot holds is refused in every mode, `yolo` too (item 561, *No secret
  out* in [Safety](safety.md#safety)). Outside `yolo` it is refused on a page that holds a password, one-time-code or
  card field, in any frame (a script reads a field's value whole, one Chrome filled in too), and from then on no text a
  browser tool
  of that chat gives back shows the values of the site's cookies, the ones long enough to be a session (a plain word
  such as `dismissed` is no session): they read `[hidden]` in the script's value, in its error, in where it led the tab
  and in what a later tool reads, and the judges and the log lines that name a refused address do not have them
  either (item 533); a screenshot shows a page as pixels, so a cookie a script wrote into its text shows there. It takes the place of AppleScript's `execute javascript`, which lands in the front window
  of whichever Chrome macOS picks when several run (item 263).
- `browser_wait_text` waits until a text is visible and says after how long. When the text does not come in time the
  call fails and still shows the page, as what changed since the last view, so no snapshot is needed to decide.
- A dialog, or a cookie consent banner, open in the viewport is listed first, under "in front of the page",
  and the line above the page says how many are open. A click that another element takes fails with the name of that
  element and what to do about it (close or answer the box, or press Escape). A page whose script holds it, so that it
  answers nothing, fails a step within two seconds, saying so, and a view of it hides a filled field it could not ask
  about (item 471).
- Password, one-time-code and card fields are never filled by a step of `browser_act` outside `yolo`: there the step
  is refused, and the browser is yours to take over; in `yolo` the run fills the field itself and the record of the
  step says why it would have asked (items 429 and 443, see [Safety](safety.md#safety)), once the review model let it
  through when the run read outside content (item 525). A login of your 1Password is the one other way in, in every
  mode (item 493): `browser_logins` lists the logins Dot may fill (titles and websites), and `browser_fill_login` fills
  one into the current page, each fill asking you first outside `yolo`. Dot fills it, not the model, and the values
  show in no result of the chat's tools. It fills only on the login's own site, never a password or a code in your own
  Chrome (`attach`: that Chrome's requests are not held, so nothing would keep a value out of an address), and not in a
  chat in which a script (`browser_eval`) ran in a tab that is still open; once it filled one, no script runs in the
  chat's tabs while that tab is open. [Safety](safety.md#safety) has the whole of it. Outside `yolo` the run never reads
  such a field's value either: the page's lines show `[hidden]` and a screenshot covers the field (item 467). A
  `<select>` that takes a card's expiry (`autocomplete="cc-exp-month"` or `cc-exp-year`) is one of those fields (item
  554). Cookie banners are declined (`decline_cookie_banners`; a click on a banner that goes away under it is no
  error, and one that fails with the banner still there is logged as `consent_decline_failed` with the page's address,
  and why the banner could not be counted when it could not).
- A conversation thread keeps at most `max_tabs` tabs open and closes idle ones after `tab_idle_s`.
- With `idle_close_s` set (seconds; 0, the default, keeps it open until the daemon stops; item 454), Dot's own Chrome
  (`own`) closes, the Playwright driver with it, once nothing used it for that long: no chat's step is under way in it,
  no run at work holds tabs in it, no page is being read in it for the web tools or watched under "Dot's computer", no
  task is being taught in it, and its window is not the one in front. The next call that needs it starts it again on the
  same profile, signed in as before (a profile keeps its session cookies, item 453; Incognito keeps nothing); the chats'
  tabs go with it. Dot's Chrome is the Google Chrome application, so while it runs macOS takes it for yours: a click on
  Chrome in the Dock brings Dot's forward instead of opening yours. Once Dot's has closed, the Dock opens yours again;
  while a run works in its browser, the mode `attach` lets Dot work in your own Chrome instead.
- `[browser] allowed_hosts` and `blocked_hosts` limit the sites of the browser tools and of `web_fetch`, and the
  addresses a call of the web-research server's tools holds (`web_scrape`, `web_extract`, `web_map` and
  `web_crawl_start`, item 583: checked as `web_fetch`'s is, and a long one is flagged as it is, see
  [MCP servers](extending.md#mcp-servers) for what they do not reach); a host also
  matches its subdomains, and an empty `allowed_hosts` means every site. An entry is read as a host is, so that one
  spelling names a host in all of them: an international name written in Unicode or in punycode, with the dot of an
  absolute name, or as a numeric address (item 554). The blocked list starts with the cloud-metadata hosts. Dot's own
  interface is never opened, whatever the lists say: this Mac on `[server] port` in any spelling (`localhost`,
  `127.0.0.1`, `[::1]` and the like), and the names of `[server] reach`; your Chrome may be signed in there. One page of
  it passes: the one a sign-in comes back to (`/oauth/callback` on this Mac at `[server] port`), which macOS may open in
  Dot's own Chrome when you sign in from Customize; Chrome opens it as a page and nothing else of the interface, and a
  run whose tab lands there still has the tab reset (item 448). A page a run showed passes too, whatever the lists say
  (`/pages/<id>` exactly, at the address `show_page` gives the run, item 601): the run opens it to see what it showed.
  It is sandboxed wherever it opens, so its scripts reach neither the API nor the UI, and anything it loads of the
  interface stays refused. A server of this Mac on another port opens as any site.
  A file of this Mac (`file:`) opens in neither browser: a page opened from disk could lead the browser to any other
  file, past the rules on reading files. The refusal tells the model what to do instead (item 596): read the file with
  `read_file`, which those rules judge; show a page it made in its workspace with `show_page`, which draws it in the
  conversation and whose Open shows it full size in your browser (item 598, [Runs](runs.md)); and write the path of any
  other file of its workspace in code in the answer, which the panel beside the conversation shows. In Dot's own Chrome
  every request is checked as Chrome sends it, not only the address a tool opens: a page (in a tab, and each hop of a
  redirect) must pass both lists, and anything a page loads (a script, a picture, a fetch, a worker) must be neither a
  blocked host nor Dot's interface, so a page of an allowed site still loads its parts from wherever it keeps them; what
  a page makes itself (`blob:` and `data:` addresses) reaches no server and passes. In your Chrome (`mode = "attach"`)
  Dot holds none of its requests, so as not to slow or stall your own tabs.
  What is not held is judged where it lands (item 554): a tab of Dot's on a refused address, and a frame of its page on
  one, is reset before anything is read from it or done in it. Every view, text, picture and search of a tab, every
  step, script, scroll, wait and upload asks first; a tab a page opens (a popup) is asked as the chat takes it in, and a
  page of the tab that goes there on a timer, after it was read, is found by the next call, which fails and says so. A
  frame is left for a blank page, with a note, and a frame that goes there while the page is read fails that read.
  Whatever the browser, the daemon itself refuses a navigation to its interface that a page of another site started
  (`Sec-Fetch-Site: cross-site`), save to the page a sign-in comes back to and to the pages runs showed. A host is read
  the way Chrome reads it (international names, the numeric spellings of an IPv4 address, a trailing dot), and an
  address with a backslash before its query is refused. A tab Dot opens waits until Dot checks each hop of its
  redirects. Chrome tells of a popup only once its page has answered, so the hops of the popup's first redirect, and of
  a redirect that a frame of another site starts by itself, are sent before Dot can ask about them (Dot's own Chrome as
  well): a hop to a refused address reaches its server, and nothing of the answer is read, since the popup or frame is
  reset when it is taken in or read. A page that cancels a request while Dot is judging it makes Chrome refuse the
  verdict ("Invalid InterceptionId"); that is logged only at debug level, and any other refusal as
  `browser_hop_unanswered`.
- `raise_window = false` (the default) keeps the window in the background while a run works: every tab Dot opens, a
  run's or a quiet read's, is created in the background (a new tab opened the usual way brings Chrome to the front of
  the Mac), and it still paints the page under Dot's computer. macOS brings a newly started Chrome to the front, and
  Chrome takes the front itself when a page opens a tab of its own (a link to a new tab, `window.open`): the app that
  had the focus is brought back right after, as long as the program that runs Dot has Accessibility. Switching tabs
  does not raise the window. With `true` the window comes to the front at the start, with each new tab and on each tab
  switch. Take over brings the tab to the front either way.
- `remote_input = true` (the default) lets you use the page of a run you hold from the screen that shows it, a phone's
  too (item 470): the run is held as for Take over, nothing comes to the front of the Mac, and your taps, scrolls and
  keys go to the page while the run waits. Nothing of what you type is kept. A tab a tap opens is not the one the
  picture shows. Settings › Browser switches it. Taking a run over, on the Mac or from the screen, first gives you a
  page no script of the run is in, when one (`browser_eval`) ran in the chat's tabs: it could have left a listener that
  copies what you type, so the chat's other tabs close and the current page is opened again from its address (a form
  it was the answer of is not sent again), and the run's next view says so (item 554).
- `headless = true` hides the window. Starting Chrome and loading the first page took about 2 seconds with it on an
  Apple-silicon Mac; later calls take well under a second. When Chrome crashes or the connection to it is lost, the
  next call starts it again.
- Chrome runs with its sandbox on. Playwright would start it with `--no-sandbox` unless told otherwise, which makes
  Chrome show a warning bar and weakens the browser that opens untrusted pages.
- Dot's Chrome is the same application as yours, only with another profile. To give it an application of its own, run
  `uv run playwright install chromium` and set `channel = "chromium"`.

## Teaching a task

The composer's "+" offers **Teach a task** (item 423). The owner shows the task once in the chat's tab: the tab comes to
the front on the Mac, and the panel shows it as Chrome paints it and lists the steps as they come: a page of the web
opened, a click on a named control, a field filled, a choice made, Enter pressed. A tab still blank goes to Google first
(item 510), and a blank tab or a page of Chrome's own is no step. The script that reports them goes into the page and
runs again in every document the tab loads, so a task that moves from page to page is one recording. It reports no value
of any field, and for a password, one-time-code or card field (a `password` input, or an `autocomplete` of
`one-time-code`, `cc-number`, `cc-exp` or `cc-csc`) not the field's name either: the step says a secret field was
filled.

**Stop** ends it, and **Write the skill** has the review model write what the owner showed: the kind of work, one line
of what it does, when it fits, the steps and their parameters, and the lines the owner's own steps made. The owner reads
it and saves it or throws it away; a saved one is a skill under the playbooks with the header of a taught skill, offered
by the `skill` tool to later runs like a learned one and listed under Skills, and **Run it now** starts the run it is for
in the chat, with the skill picked. No run stands behind it, so no failure retires it: whether it still fits is the
owner's to say, and a run of its kind learns a skill of its own rather than rewriting the owner's.

A review model that is not one of the strong ones may give the steps or the parameters as one text instead of a list
(a JSON array in a string, or a line to each step, numbered or with a bullet), or leave the parameters out (a skill with
none): Dot reads them as the list they mean (item 543). All the steps run together in one line are no list of them. An
answer that is still no skill (no steps, one line of them, more than twenty, a kind too short) is asked for once more,
and when it fails again the panel says which field was wrong and how (`kind: Field required; steps: Input should be a
valid list`), not only that the answer was no verdict.

A browser without a window refuses to record (there is nothing for the owner to work in: turn `[browser] headless` off),
a second recording is refused while the first goes on, as is one started while a run works in that chat (a run's own
clicks are real ones, and a step of them would read as the owner's; while a run is at work in the chat mid-recording, its
steps are not taken). A recording is only ever of the tab it was started on and only while it goes on: closing the panel,
or the tab, throws it away. The recorder reports through a binding in the page's own world, so a script of the page
can call it too and add steps the owner never took, as the labels of the page's own buttons already are the page's words
(item 554): a recording keeps at most 400 steps, and when it is full the skill writer is told the rest was not kept. The
owner reads the steps, and the skill drafted from them, before saving it, as above.

## Computer use

The desktop tools see and drive any application through the accessibility tree and, where an application has none,
through screenshots and coordinates. macOS must grant the app that runs Dot three permissions: **Accessibility**,
**Input events** and **Screen Recording**. `dotx desktop check` shows which it has and `dotx desktop setup` asks macOS
for the missing ones and opens their Settings panes; Settings › System › Permissions in the web UI does the same for the
daemon, and restarts the service once a permission is granted (item 409).

- `desktop_apps` lists the running applications and `desktop_open_app` brings one to the front, starting it when it is
not running: by name (Terminal), bundle id (com.apple.Terminal) or bundle path (/Applications/Terminal.app). A path to
anything else (a binary) is refused at once with those three forms, instead of being retried against Launch Services.
A bundle that is not on the sealed system volume (`/System`, and not the data volume mounted at
`/System/Volumes/Data`, the letters of the path read without case as the Mac's file systems read them) may be one a run
made, which runs outside Dot's sandbox: it asks first, as a step outside the sandbox (item 418), whether it is named by
its path or by a name or a bundle id Launch Services resolves to one. The bundle is resolved again as the call starts and
one that leads elsewhere by then is refused, as a path a file tool names is (item 181), so a link moved while the call
waited for its approval opens nothing. Outside yolo a run's command opens nothing (`open` is refused in the sandbox), so
this tool is how a run's command starts an application; an AppleScript can still start one itself (see
[Safety](safety.md#safety)).
A name that fits several running copies of one application, "Google Chrome" while your Chrome and Dot's own both run,
means the Chrome Dot's browser works in (item 343); a pid names any one of them. Chrome's remote debugging is the
owner's: a desktop step on chrome://inspect's switch or on the Allow Chrome asks is refused in every mode but
`yolo` (item 433).
- Work inside a web page goes through the browser tools, which act in the run's own tab whatever other Chromes run;
  the desktop tools are for what lies outside the page: a native dialog, a file picker, an extension's window. A script
  sent to Chrome with AppleScript reaches whichever Chrome window macOS picks; `browser_eval` runs in the run's tab.
- `desktop_snapshot` lists an application's window as elements with a role, a name, a value, a state, a position and a
  ref (`a12`). A second view lists only what changed; `query` finds elements anywhere in the application and
  `scope = menu_bar` lists the menu bar. `desktop_read` reads one element's full text. `desktop_clipboard_read` reads
  the text on the clipboard, which can hold a password you just copied from your password manager, so it asks in every
  preset, as `pbpaste` and an AppleScript that names the clipboard do (item 534); and outside a yolo run the sandbox, where
  it is on, closes the clipboard to a run's commands as well; a yolo run asks nothing (item 443).
- `desktop_act` runs up to `max_batch_steps` steps in one call (25 by default; a longer batch is cut the same way):
  `click`, `double_click`, `right_click`, `hover`, `press`,
  `focus`, `set_value`, `type`, `paste`, `key`, `scroll`, `drag`, `menu`, `wait` and `activate`. A step names an element
  by its ref or a point by its coordinates. `press`, `focus` and `set_value` work through accessibility without moving
  the pointer; `click`, `hover`, `scroll` and `drag` are real pointer events; `type`, `paste` and `key` are keyboard
  events. A `type` text longer than `paste_threshold_chars` (40) without a line break or a tab goes in through the
  clipboard, which is put back afterwards (also when the clipboard refuses the text or the paste fails, item 555),
  unless the field is secure, is for a credential or a payment, or cannot be read, or the text looks like a key. After
  steps that changed something, the window is read once the application has
  announced no change for `settle_quiet_ms` (120 ms), at most after `settle_ms` (350 ms); a change it makes later
  without announcing anything in between shows in the next view.
- In a terminal application (Terminal, iTerm2, Ghostty and the like) text and keys are shell input. Dot keeps the
  command line it typed there since the line was last empty and judges it whole, as a `shell` command, when Return or
  a Ctrl- key that may run it goes in, so a command typed in parts is judged as one, from the home folder. After a
  click, a focus, a menu, a drag, a Cmd shortcut or an arrow key the line cannot be told, and the next Return asks, as
  the first one in a terminal Dot has not typed into does; Ctrl-C starts a line Dot knows again. Other steps there run
  commands too, which Dot cannot read: the Run button of Terminal's New Command dialog, a menu item (Shell › New
  Command…, Edit › Paste), the value of a field set, a press or a click anywhere in its window, a drag. Each is judged
  as the Return of a line it cannot tell, a `shell` step that every preset asks about (item 555); a `hover`, a `scroll`,
  a `focus`, an `activate` and a quit are not. The card of such a question shows the command lines the step runs, as
  Dot typed them from the line it knows (key-like text masked), and what the judgement found in the step.
- An editor with a terminal of its own and an application that runs scripts (VS Code and its forks Cursor and
  Windsurf, Zed, the JetBrains IDEs, Script Editor, Automator, Shortcuts) are no terminal: what is typed in them is
  mostly code, so it is not read as shell lines. What asks, as a step about `privilege` (item 555), is the step that
  runs what is written there: a line break typed or pasted, Return, F5 or Cmd-R, a button or a menu item whose name
  says that it runs (run, play, execute, debug). That is all Dot reads of them. It does not read the command typed in
  the terminal of any other editor or application that runs what it is given, a button that runs under another name,
  or a line of the clipboard that a paste puts to a program that does not wait for Return (`desktop_clipboard_write`,
  then a paste): the guard reads what Dot types into a terminal application, not what another application makes of it.
- Return presses the button that has the keyboard and the default button of the window (item 555): a `key` step of
  Return, Enter or Space, and a `type` text with a line break, are judged by the names of those buttons as a click on
  them is, so Return on a Pay, a Send or a Delete asks as the click does.
- A click at coordinates is judged by the control accessibility finds under the point and by the title of that
  control's own window, so that Dot's own window is known behind another one whether or not macOS lets Dot see the
  screen (item 555). An application that does not say what is there (busy, or not answering) gets no click: the step
  fails and tells the run to take a snapshot, which wakes the accessibility of an application that builds it on demand,
  and press or click by ref, or to try again. An application that has nothing to say there (it does not implement
  accessibility, or gives no value) is a click on nothing, as before, since coordinates are for what accessibility
  does not show.
- A step the owner was asked about is read again before it goes (item 555): when the application, the kind of window,
  the control, its name or the default button is no longer what the question was about (a page put a "Buy now" where
  the "Send" was while they decided), the step is not done and the run is told to look again. While a batch waits for
  the owner's answer it holds the desktop, as they may be doing the step themselves on this screen; a run that asks for
  the desktop meanwhile is told so at once, instead of waiting until its own tool times out.
- `desktop_screenshot` captures the screen with ScreenCaptureKit as a JPEG of at most `screenshot_max_edge` pixels
  (1366 by default: about 1 600 tokens on the hosted providers, against about 2 500 at 1920). The windows of password
  managers and Keychain Access are painted black, known by their application's bundle id and not only by its name,
  which is in the language of the Mac (item 555).
- `desktop.keep_awake` keeps the display awake while Dot works. Password managers are refused in every mode, and so
  are secure fields outside `yolo`, where the step is the user's; in `yolo` the run types the field itself (item 443),
  and a step the review sees take a credential keeps that reason in its record (item 429, see
  [Safety](safety.md#safety)).
- `dotx desktop apps`, `dotx desktop snapshot [app] [--scope …] [--query …]` and `dotx desktop screenshot PATH` show
  what the agent would see.
