# Safety and privacy

## What leaves this Mac

Dot sends nothing to the people who make it: there is no telemetry and no crash report. The desktop app looks for a
newer version of itself as it starts and from its menu, a request for `latest.json` of the latest GitHub release that
carries nothing of yours, and on your yes downloads that version from there ([the desktop
shell](daemon.md#the-desktop-shell)). What leaves the Mac is what a run needs, sent where the configuration points:

- **To the models: the run's conversation.** Each call sends the goal, the messages, what the tools returned (the text
  of the files and pages the run read, what commands printed, never the value of a secret field of a page outside
  `yolo`, see Secrets below) and the screenshots the run takes or is given. By default
  every role (`main`, `vision`, `fast`, `review`) is a cloud model of Ollama: the Ollama server on this Mac passes each
  call on to Ollama's servers once it is signed in. Dot's own work on what its runs did goes there too: lessons,
  playbooks and the nightly reflection to `review`, the shortening of a long run's conversation to `fast`. OpenAI,
  OpenCode Go, Anthropic and OpenRouter get it only when a role, a helper or a standby model names one of their models;
  OpenRouter passes each call on to the lab whose model it is. OpenCode Zen's free models (`opencode:`) get the whole
  conversation of a run that runs on one, through OpenCode's own agent, and the providers of some of them may use what
  they get to improve their models (see [Models and providers](models.md#models-and-providers)).
- **To the embedding model: each note and each search of the memory.** It is Ollama's `bge-m3` on this Mac by default;
  with `[memory] embed_provider = "openai"` the texts go to OpenAI (see [Memory](memory-and-schedules.md#memory)).
- **To the search engines: the query.** The `[web] search_backend` ladder asks the owner's SearXNG instance when
  `[web] searxng_url` names one (the instance passes the query on to its own engines, Google and the others), then
  Ollama's hosted search when `OLLAMA_API_KEY` is saved, then DuckDuckGo and Brave Search, then DuckDuckGo in Dot's own
  Chrome; Anthropic's search (relayed by `[models] search`) joins when `ANTHROPIC_API_KEY` is saved, and
  `[web] anthropic_search = "off"` leaves it out.
- **To the sites a run visits: its requests.** The web tools fetch public addresses only, unless
  `[web] allow_private_hosts` allows more (the owner's SearXNG instance is configuration and is reached wherever it
  is, a loopback one too); a page the browser reads for them in place of a plain request a site turned away is held
  to the same rule, and one it was served from a private address is not given back (item 554, see
  [Tools](runs.md#tools)); Dot's browser carries the cookies of its profile (Dot's own, or your Chrome's
  when it is attached). `web_extract` sends what it read to the `fast` model, and a page a run shows in the conversation
  loads its libraries from public CDNs (see [Tools](runs.md#tools)).
- **From the shell: whatever a command sends.** The sandbox closes Dot's own files, not the network; a run that has read
  untrusted content asks before a line that sends data out (see [Safety](#safety)).
- **To TypeSafe, once its key is saved: the texts it judges.** `judge_items` and the tools of posts and campaigns send
  the texts they judge; with `[judge] provider = "typesafe"` the judge of clicks and checks sends what it judges (a
  button's words, the page's address without its query, its title and visible text). `[judge] provider` is `"none"` by
  default. With `[agent] step_router = true` every step sends the goal and up to 1,500 characters of what the last step
  brought back, with the secrets Dot holds and keys of a known form masked (item 551).
- **To a phone, once you turn its notifications on: a line.** A title, up to 140 characters of the question, of a
  draft's recipients and subject (a message's words, item 314) or of the result, and the page to open, encrypted for
  that phone's browser and carried by its maker's push service (see [Dot on the phone](phone.md#dot-on-the-phone)). The
  first line of the morning brief goes the same way (item 570): the model wrote it from what the run read, mail of
  others among it, so it may repeat what a sender wrote.
- **To your own devices through Apple, once you switch iMessage on: Dot's answers, and what it tells you on its own.**
  Each answer (masked of the secrets Dot holds, cut to about 2,000 characters), the line that says a run waits for you,
  the notices a message of yours earns, and what Dot tells you on its own (item 582: the first line of the morning
  brief and each tell of a watcher, that is a title, up to 400 characters of text and the link to the page it opens;
  the text may hold a mail's sender and subject, or what a sender wrote) are sent into your conversation with yourself
  through Messages, to Apple's iMessage servers and on to your devices, as any iMessage is. What you send there is read
  from Messages' database on this Mac (see [Channels](channels.md), item 572).
- **To the browser's makers, if you use the microphone: your voice, from your browser; and, when the Mac has no voice of
  the language, the text read aloud.** The composer's microphone and the reading of answers aloud (item 574) are the
  browser's own speech recognition and speech synthesis: Safari and Chrome may send what you say to Apple's or Google's
  servers to recognize it (the browser's own settings say whether they do). In the desktop app the recognition is
  WebKit's, macOS's own, which may send what you say to Apple as Safari's does, once macOS lets the app hear (item 593).
  An answer is read aloud with a voice the browser keeps on this Mac when it has one for the language (`localService`:
  Dot picks it, and macOS's own voices send nothing). A network voice, such as Chrome's "Google …" voices, sends the
  text it reads to its servers; the browser speaks with one only when the Mac has no voice of that language, and
  Settings › General › Voice says so. Dot sends nothing itself: it only gets the words the browser hands back, into the
  box, and sends nothing until you press send.
- **To the model, for the morning brief: Dot's record of itself.** The brief's run (item 570, see
  [Dot comes to you](proactive.md)) is given what waits for you, the runs that failed since the last brief, the
  campaigns at work and the schedules of the day, as lines cut to 160 characters with their secrets masked, and what
  its mail and calendar reads return; it goes to the model the run runs on, as the rest of a run's conversation does. It
  is a `research` run, which cannot send, change or delete anything. No other service is asked.
- **To the fast model, for a watcher that has your words: each new mail or notification.** A mail watcher (and a GitHub
  watcher you gave words, item 571, see [Dot comes to you](proactive.md#watchers)) asks the `fast` model one question
  for each new mail or notification, with your words: a mail's sender, recipients, subject and first lines, or a
  notification's repository, kind, reason and title, in the fence of the mail tools. A calendar watcher, and a GitHub
  watcher without words, ask no model. A mail that cannot be judged is told to you, so it is not dropped.
- **To GitHub, for a GitHub watcher: the request for your notifications.** `api.github.com/notifications` with your
  personal access token, every few minutes, as a conditional request; it carries nothing else of yours.
- **To MCP servers and plugins: what their tools are given.** A server's own process reaches what it reaches; none is
  set up by default (see [MCP servers](extending.md#mcp-servers)).
- **To your Google and Microsoft accounts, once you sign one in: what the mail and calendar tools ask.** `mail_search`,
  `mail_read` and `calendar_events` ask Gmail and Google Calendar (`googleapis.com`) or Microsoft Graph
  (`graph.microsoft.com`) for your messages and events, with the words of a search (see
  [Mail and calendars](runs.md#mail-and-calendars)); a mail or calendar watcher asks them for the newest mail of the
  inbox or the events of the next minutes every few minutes (item 571).
- **To 1Password, once you connect it: the requests of `op`.** Its service account reads the vault you shared with Dot
  through 1Password's servers (see [1Password](extending.md#1password)).
- **To GitHub, when you install a plugin of the gallery: the request for its archive.** It names the plugin's
  repository and the commit the gallery pins, and carries nothing of yours (see
  [Customize › Plugins](extending.md#customize--plugins)).

The record of the runs, the notes, the logs and the screenshots stay on this Mac in Dot's folders (see
[Configuration](configuration.md#configuration)), and a key goes only to its own service. To keep a run's conversation here too, give
every role a model of the Ollama server on this Mac (one whose name has no `cloud`), leave the helpers and standby
models empty or local, and keep the embeddings on Ollama; the searches and pages a run asks for still go to the sites it
reaches (item 216).

## Safety

Before a tool acts, the policy is told what the action does, what it touches and which flags it carries. It answers
allow, ask, hand off to you, or deny.

**Run modes.** `task` (the default) and `chat` can do whatever the policy allows. `research` is read-only: it can read,
search, browse and look at the screen, and a research run can only delegate to research runs. The one file it may write
is its notes file (`write_notes`, below), and it may save memory notes.

**One-time codes from your own Mail and Messages.** With `[policy] code_sources = ["mail", "messages"]` (empty by
default), the agent has `code_fetch(app, sender, subject, within_minutes)`: it reads the newest message of Mail (through
AppleScript) or of Messages (its database, read-only; the program that runs Dot needs Full Disk Access, and `dotx
doctor` says whether the database opens) from a sender or with a subject within the last minutes, and returns the code
alone, with who sent it and how old the message is; the message never reaches the model. It is a high-risk action of
the Mac: the aggressive preset allows it, balanced and cautious ask, a research run has no such tool. Typing the code
into a field is the run's in `yolo` (item 443) and yours in every other mode (item 429), where Dot hands the field to
you.

**Pre-approved spending.** `[policy.spend]` lists sites with caps, for example `"namecheap.com" = { per_purchase = 20,
per_day = 50 }`. Dot reads the amount of a payment step from the labels of its controls ("Pay €19.90") and from the
total on the page, and takes the largest (item 385: a coupon or a basket line in the same step cannot stand for the
payment), so that a misread errs towards the hand-off. A batch of browser steps carries one payment step at most (item
561): a click that pays, by its control's words or by the typed judge's reading; filling in a card field is not one.
Two payments in one call would pass for the larger of their amounts, so the call is refused and the model sends them
one call at a time, each decided on its own amount; this holds in every mode, `yolo` too. Under both caps the step is a
question instead of a hand-off (`auto` lets the review model answer, `ask` asks you once, and a `yolo` run asks
nothing), it
is recorded with its amount, shown in the timeline, and counts against the site's day.
The payments of a site are decided one at a time, each from what the ones before it spent, so two runs paying at once
cannot both take what is left of the day. Over a cap, on a site not listed, or when the page does not show the amount,
the money hand-off holds where the modes ask; a `yolo` run pays it and what it spends counts against the site's day
(item 443). The caps are plain numbers compared with the number on the page, whatever its
currency, so list sites that bill in one currency. Empty by default.

**Decision order**, strictest first:

1. *Auto-review* denies actions that read secrets or send data out (`secret_access`, `exfiltration`), whatever else is
   configured; a `yolo` run waives it, and the steps after it decide (item 433).
2. The *run mode* denies what a research run may not do.
3. *Dot's own files*: a change of them (`self_change`, below) is a question to you in every approval mode but `yolo`,
   which no rule of yours and no review model answers in your place (item 398). Allowed, the run makes the change.
4. *Hard stops* hand money and credentials to you (`financial`, `credential`): Dot stops and asks you to do that step
   yourself. A payment under the pre-approved caps of its site (above) is a question instead of a hand-off, unless
   one of your rules hands that step to you (item 385): the caps stand in for the money hand-off, not for your rules.
   A click is about money when its control's label asks for it: at the start of the label or of a sentence in it,
   after a stop, a dot, a bar, a dash, a colon or a new line ("Pay $19.90", "Buy", "Order now", "Lamp — $9.90 —
   Buy", "Öde", "Maksa"), anywhere in it ("Place order", "Confirm and pay", "Complete checkout", "Satın al"), or with
   an amount in the label next to a verb that offers something for it ("Rent HD $3.99", "Subscribe for €4.99/month",
   "Book 2 tickets · $40", "Book 1500 TL", "Book: $199"). "Order", "Book" and "Reserve" with an amount count unless
   the word is followed by "#" or by "summary", "history", "details", "status", "number", "total" or "price", which
   make the label a record ("Order #4521 · $45.00 · Delivered", "Order summary · $45.00"); a row of past orders that
   shows its number without "#" hands the click off. A dialog that asks to confirm is judged by the same words,
   without that exception: there, an order's number is the order about to be paid. The way to the
   payment page ("Checkout", "Continue to payment") is an ordinary click, so the run fills the order and the button
   that pays is the one handed to you (item 522). Enter or Space pressed on a button, which presses it, is judged by
   the button's label as a click on it is (item 561), and so is Return on a focused or default button of an
   application of the Mac (item 555).
5. *The guard* (item 429): a message sent for you (`message_send`: an email, a chat or text message, a reply or a
   forward, through AppleScript, a messaging app, a Send button, an MCP tool whose name says it sends or posts, or a
   mailer of the command line) and a post in your name (`public_post`) wait for a yes in every mode that asks, whatever
   your rules say: yours in `supervised` and `ask`, the review model's in `auto`, where it answers the policy's
   questions; only a rule that hands the step to you is stricter. A rule that allows it does not answer for you. A login
   filled from your 1Password (`vault_fill`, item 493, below) is guarded the same way, except that in `auto` you answer
   it, not the review model. A `yolo` run asks nothing and makes the step, its record keeping the reason it would have
   asked for (item 443).
   What the policy cannot tell for a message is not guarded: a key that sends in a web page's chat box, a page's own script
   (`browser_eval`), a program of the shell that talks to a mail server or a chat's API itself (`curl`, a script).
   For an AppleScript or JXA script (the tool `apple_script`, and `osascript` in a command line) the guard reads the
   script's words (item 561): `send` and `make new outgoing message`, also when JavaScript writes a letter of the verb
   with an escape (`"s\x65nd"`), and the properties of a Mail rule that send later (`forward message`, `redirect
   message`, `reply text`), ask as a send. A script that may send in words the guard cannot read asks as a send too.
   Some say nothing it can read whatever application they drive, and ask whichever they name: a raw Apple event
   (`«event ichtsend»` is Messages' own `send` by its code, `«event emsgsend»` Mail's); text the script runs as a
   script (`run script`, `load script`), or builds and runs through the Objective-C bridge (`NSAppleScript`,
   `NSClassFromString`, `NSAppleEventDescriptor`, `performSelector`, AppleScriptObjC's `current application's NSTask`
   and `NSProcessInfo`); a script file (`osascript send.scpt`); and a script the command line puts together as it runs
   (`osascript -e "$S"`, `-e "…$(…)…"`, a here-document or here-string that holds a variable, `xargs osascript -e`,
   `find -exec osascript -e {}`): the line shows words, not the script that runs, even when the variable is the line's
   own. So do keystrokes (`keystroke`, `key code`, `keyCode`) and a button pressed through System Events' UI scripting
   (`click`, `perform`, `actions`: `click button 1 of toolbar 1`, `perform action "AXPress"`, a JavaScript `.click()`
   or `.actions[0].perform()`), whatever application they are pressed in: a key goes to the application in front, and
   a process is found by a filter, a built or a localized name as well as by its own, so the script cannot say whose
   Send it presses (the structural option after the third review of item 561; a script that only copies or pastes
   through keys asks too). A shell line a script puts together as it runs (`do shell script` or `doShellScript` with
   anything but literal text, and the line Terminal's `do script` types) is code the guard cannot read, like such a
   command line: every preset asks about it, and as a send too, since the line may be `osascript` with a script that
   sends. Others are how
   scripts of every application are written, and ask when the script names Mail or Messages (by name, bundle id or
   path) or builds the name of the application or the process it drives: names it by anything but one string (a
   concatenation, a variable, a number or a filter: `application ("Mess" & "ages")`, `application a`, `application
   id (…)`, `process 1`, `process whose …`; JavaScript's `Application(…)`, `byName(…)`, `processes[…]` with anything but
   one string, or `whose(…)`; `current application` is none). They are read by what they are called, not by how they are
   called: JavaScript that builds code or a name as it runs (`eval`, `Function`, `constructor`, `Reflect` and `Proxy`
   wherever they stand; a method called through `.call`, `.apply` or `.bind`; a bracket that is neither an array
   literal nor a number: `m["se" + "nd"]`, `m?.[verb]`, `{[verb]: go}`, `m /**/ [verb]`. An array literal is one after
   `=`, `(`, `,`, `:`, `[` or `>`, or at the start; the comments and what the strings say are left out first, and
   where a slash or an HTML-like comment keeps the guard from telling them apart, every bracket that is not a number
   asks); the Objective-C bridge (`ObjC`, `$.NS…`, and AppleScriptObjC's `use framework` and `current
   application's`, which also make a script high risk). A plain read of Mail or Messages (`get subject of messages
   of inbox`, `messages[0].subject()`) asks nothing; JavaScript that indexes by a variable (`messages[i]`) does. What
   the guard does not read: JavaScript that builds a name with `with`; an application other than Mail and Messages
   that sends through its own dictionary (Outlook, another mailer: there the verb is the only word it reads); what a
   script can reach, which it tells by the names in the script, so that a localized name of Messages (Mesajlar) or an
   application reached through a symlink passes; a shell line that JavaScript runs through a name it builds as it runs
   (`a["doShell" + "Script"](line)`, `a.doShellScript.call(a, line)`) in a script that names neither Mail nor Messages,
   which is not read as a shell line at all, so that what it runs is held by the sandbox alone; and what an
   application does once a script has handed it the work. macOS does not hold a sandbox to Apple Events (see below),
   so what stands in the way of such a script is the Automation permission you gave Mail and Messages.
6. *Your rules* come next. The first enabled rule, in order, whose tool pattern and regular expression match the action
   decides, with one of four answers: *do it* (`allow`), *do it, whatever it has read* (`preapproved`: the question
   untrusted content raises is skipped too), *ask me first* (`ask`) or *hand it to me* (`handoff`: Dot stops, says what
   it needs done and waits; on a browser step you can take the browser over, do it and give it back; when you say you
   did it, the run goes on from there). A rule that hands the step to you decides wherever it stands, the first of them
   when two fit (item 481): an earlier rule of another answer does not keep it from deciding, so an exception to a
   hand-off goes in the hand-off's own expression (a lookahead such as `(?!.*--dry-run)` works). Once a rule fits, only
   the rules after it that hand off are read. The expression is searched, case-insensitive, in the action's one-line
   summary (a shell command's starts with `Run: ` and names the folder it runs in when the call names one, item 483,
   so an expression can fit that folder too), a line break and its target (the command, the path, the address);
   one that takes longer than 0.25 s to match is stopped and the action refused, as for a rule that cannot be read, so
   that a pattern that backtracks for long cannot hold the daemon (item 387). A rule
   reads as a sentence in Settings › Rules, "When Dot would use `shell`, matching `\bgit push\b`: ask me first", and
   is managed there or through `/api/rules`; the page lists the rules in the order they are checked, a rule's arrows
   move it one place up or down that order, and **New rule** opens the two ways to write one (with no rule yet, the
   page offers three sentences to start from, one of which opens them with it in the box; under the rules it tells
   what each of the four answers does, and above them it lists what is always on, item 565). A rule that an earlier
   enabled rule fits wherever it fits (no expression, or the same one, on a tool pattern that takes in its own: `*`,
   the same pattern, or one that matches its plain tool name) never decides, nor does a rule that one handing the step
   to you fits wherever it fits, wherever that one stands, while a hand-off is kept from deciding only by an earlier
   hand-off; the page says so with the number of the rule that decides in its place (items 449 and 481); whether two
   different expressions overlap it does not tell. Under each
   enabled rule, the page says how it was used on the steps of the last 30 days, with today's rules (`GET
   /api/rules/usage`, item 473): how many steps it fits, how many it decides (as the first rule to fit, or as a
   hand-off wherever it stands), how many of those ran in yolo runs or were settled before any rule, and which rules
   decide the others. Before a rule is
   added, **Test on the last 30 days** under its fields (`POST /api/rules/preview`, item 469) tries it on the steps of
   past runs, as the policy matches rules, and writes nothing. It says how many steps it fits and which earlier rules
   decide some of them first. Of the rest, it counts apart the steps of yolo runs, which ask nothing whatever the rules
   say, and those settled before any rule is read, by the checks above that no rule changes (money, credentials,
   messages and posts, a login from your 1Password, Dot's own files, the always-on review, a research run, a spend
   within its caps; a rule that hands the step to you does take the place of the question about a message, a post, a
   login or a spend). The steps it would decide it
   compares with what was decided then: the decision a step's record keeps since item 468, or for a step the gate did
   not let through (refused, declined, handed to you), what the policy said of it before, in the `policy` event of its
   call. It says how many would ask more, how many less and how many the same, with the taint the gate weighed then
   (kept since item 474; for an older step, the run's taint before its result): an allow rule still asks about a step
   that reaches outside while the run is tainted. A step with no decision on record (held by another run, refused by the
   review model, from before item 468, or an OpenCode agent's that ran before item 474 or that the gate did not let
   through) is counted as such. Then come the newest steps it fits, each leading to its run. A drafted rule is tried at
   once. A try-out reads at most 20,000 steps and two seconds, the newest first, and says so when it read only those; a
   pattern that takes too long on a past step stops it, with that said, as the daemon would refuse the step. Only steps
   still in the database count, and a batch of browser or desktop steps is one step, as its record keeps it. It can
   also be said in your own words ("let Dot run git status and git
   diff without asking me"): the review model drafts its parts, the tool, the answer and the commands or words the
   action must mention, and Dot writes the expression from them, so that a rule that lets Dot run a command fits it
   run alone with plain arguments (after a `cd` at most; nothing chained to it, substituted into it or sent on with
   `|` or `>`), and a rule that stops Dot fits the command or the word wherever the action mentions it, as a whole
   word (`x.com` does not fit `netflix.com`). A rule that lets Dot act in the shell is made from commands only: a draft
   that gives words for it (for the shell, or for `*`, which covers it) is refused and the review model asked once
   more, since a word fits `git push; anything` as well as `git push` (item 561). The same goes for the AppleScript
   tool (`apple_script`, or a pattern that covers it), which runs shell lines with `do shell script`: a word fits a
   script that runs a command outside the workspace as well, and the tool has no form for commands, so a rule that lets
   Dot act in it is for every use of the tool. The draft fills the form, its
   description the sentence the rule reads
   back as ("For “git status” run alone in the shell, …: Dot does it without asking."), and applies once you add it.
   Words that cover every action ("Dot can run everything without asking me") draft a rule for every tool ("For every
   use of any tool: Dot does it without asking."), which the protections that are always on still bound; words that
   make no rule are answered with the reason, in English.
   An approval card offers *Make a rule of this*, which opens the tab with a sentence about the action in the box; the
   card of what no rule decides (money, credentials, a message or a post in your name, a login from your password
   manager, a change of Dot's own files) offers none and says so (items 429 and 559).
7. *The preset* decides everything that is left; in the approval mode `supervised` an action it allows still asks you
   when it changes something (see [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)):

| Preset                 | Asks before                                                                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `aggressive` (default) | privilege, system settings, public posts, messages sent for you, destructive actions outside the workspace, and a read that may reach a protected location or the clipboard |
| `balanced`             | all of that, plus anything destructive or installing, anything of high risk, and writing or shell commands outside the workspace                                            |
| `cautious`             | everything that is not read-only                                                                                                                                            |

An upload (the flag `upload`: a file chosen in a page's upload control with `browser_upload`, which many pages send as
soon as it is chosen; a `curl -T` or a form sent with a file; a push; a copy to another machine) is in no preset's
list, so what each does is this (item 561): `aggressive` and `balanced` let one go until the run has read untrusted
content and ask from then on (the question untrusted content raises, below), `cautious` asks for every one, and a
`yolo` run asks nothing, but has the review model check it once the run has read outside content (*Outside content in
yolo*, below). An upload stays medium risk unless a file of it is in the home folder, the system folders or Dot's own
(`exfiltration`) or protected (`secret_access`), which every mode but `yolo` refuses; that is why `balanced`, which
asks for high risk, does not ask for every upload. The page an upload leaves is a stranger's to write, so what the tool
shows of it marks the run untrusted, as any page does.

A shell line is destructive outside the workspace when it removes, cuts or moves away a file there (item 385: `mv` takes
its sources from where they were), and when it replaces one that is there: a redirect that empties it (`>`, `>|`, `&>`),
a copy or a move onto it or into a folder that holds a file of its name (not `cp -n`), `ln -f`, `tee` without `-a`, `dd`
without `conv=notrunc`. The shell keeps no copy of what it replaces, unlike the file tools, whose changes `dotx undo`
writes back.

Who answers what the policy asks depends on the approval mode of the run: you, a review model or nobody (see
[Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)). The second step holds in every mode; a hand-off rule of yours holds in every mode that asks, and a `yolo` run waives it
with the rest (item 443). What a `yolo` run waives is every question and every hand-off below it (item 443): the
refusal of the first step, the questions of the third step, of your `ask` rules, of the preset and of untrusted
content, the hand-offs of the fourth step and the questions of the fifth, and it records each with the reason it would
have asked for; a run marked untrusted keeps two checks of its own (item 525, *Outside content in yolo* below). Without
the first step, a `yolo` run can also
read Dot's own key (`api-token` in Dot's home) and with it do what the web UI does, answer a question of the policy
among it: in `yolo` the guard stops the steps of the work, not a run set on getting around it.

**Untrusted content.** Web pages, screens, MCP results and the mail and calendars of the accounts you signed in (item
424) can be written by third parties. Dot wraps them in a fence with a random marker per call (`<web_content …>`,
`<screen_content …>`, `<account_content …>`) and tells the model that what is inside is data,
never instructions; only the end that carries the marker closes a fence, and a closing tag inside, in any case, is
broken. The reply of a teammate that read such content and the files you attach to a message are fenced the same way.
What a shell command printed after it reached the network (`curl`, `ssh`, `git clone`, `gh`, a line
the guard cannot read), what a terminal program prints, and a find_help answer that lists skills or MCP servers that
other agents of the Mac set up count the same way, unfenced, and so does reading back the files Dot keeps of such content
when it is too long for one result, under `<workspace>/crawl/` and `<workspace>/mcp-results/` (item 494): `read_file` of
one, a `search_files` match in one or a search of one, a shell line that names one or runs in one (the words of the
line are read, so a line that reaches them otherwise, by a variable or a search of the workspace, is not seen); a
failed `edit_file` of one names none of its lines. A page that comes back from a step counts the same (item 561): what
`browser_act` (a click, an entry), `browser_upload` and `browser_fill_login` show of the page they leave is marked
untrusted too, as what `browser_eval` and the desktop tools show already was, so a click on Next that brings up a page
that says "run curl -T …" leaves the run marked even after your next message cleared the mark and the chat's tab
stayed. A run that has seen such content is marked untrusted, and from then
on outward-facing or lasting actions (messages, posts, follows and likes, privilege, system settings, saving a schedule
or a note, installing software (`brew install`, `pip install`, `npm install -g`, `cargo install`, `uv tool install`) or
running a script the line downloaded (item 523), or a blob it decoded and ran (`base64 -d x | sh`, or a decode to a
file the line then runs; item 528), and a shell line that sends data out: an upload or a form, a push, a
copy to another machine, a change on a code host, inline code that reaches the network or runs a command of it, and a
read that may reach a protected location, item 531) ask even when the preset or an `allow` rule would let them through;
only a `preapproved` rule skips the question. A
project's own install (`npm install`, `uv sync`, `uv add`) goes ahead. The error of a call that reached outside marks
it too when it may hold such words (item 551): what an MCP server answered a call with, which the model reads inside
the fence as data, and the error of a search whose engines answered with an error page; a failure in transit does not.
A reaction on a message of Dot's (item 586) never clears the mark: while the run works it reaches it as a note
from Dot, not as your words, which vouches for nothing and is no instruction to the review model, and it approves no
step (the note says so); after the run ended it starts a follow-up run that starts as untrusted as the run of the
message, or the chat's last, ended, whose goal holds none of Dot's words and whose quote of the message is no
instruction (see [Reactions](runs.md#reactions-on-dots-messages)). A 📌 makes a note marked as read from outside
content when the run read some. A message you send to the run clears the mark; your answer to a question of a run you
watch does not,
whether the run waited for it or went on (item 508), as it
answers what the run asked rather than sending it on its way: the run it reaches stays marked, and the run it starts
once that run has ended starts as untrusted as that run ended. A delegated run starts as untrusted as its parent, a
resumed run as the run it resumes was when it was cut off, and the run that tells a chat that its command in the
background ended as the chat's last run ended: that message is Dot's, not yours, and so is the note that tells a run at
work of such an end, which clears nothing. The run a watcher starts for a mail, an event or a notification (item 571,
see [Dot comes to you](proactive.md#watchers)) starts untrusted whatever it goes on from, since what a stranger wrote
set it off: its goal is your own words and the item stands in its first message, in a fence. A message you send starts
a run unmarked. A note a run saved while it was marked keeps that origin (`tainted`): it is never among the notes a run
starts with, is handed while it works or as a site's lessons (item 384), a `memory_search` that returns one marks the
run that read it, so its next save asks again, and Settings › Memory and `dotx memory list` say of such a note that it
was saved while reading outside content.

**Outside content in yolo** (item 525). A `yolo` run asks nothing, and a run marked untrusted may be steered by what a
stranger wrote. Two checks hold for such a run, and neither asks you:

- *The taint check.* Before a yolo run marked untrusted does a step an instruction planted in what it read would be
  after (a message, a post, an upload or data sent out, a payment, a credential, a login filled from your password
  manager, privilege or system settings, a schedule or a note it saves, an install, a change of Dot's own files, a read
  of your secrets, a deletion outside the workspace), the review model (`[models] review`) judges it against your own
  instructions, as it does in `auto`, with the run marked untrusted. A step it refuses is refused: the run is told why
  and goes on with the rest, and the record names the review model. Any other answer, `escalate` too, lets the step run
  as yolo runs it. A follow or a like is left to the pace of its site. A review model that cannot answer fails the step
  with its error. `[policy] yolo_taint_check = false` turns the check off.
- *No secret out.* In every approval mode, `yolo` too, whatever the preset or your rules say, a run marked untrusted has
  a step refused whose words would send a secret Dot holds out of the Mac: a key read in this process (saved with `dotx
  auth set` or in Settings), a value of `<home>/.env`, or text in the form of a key or a private key. A request, a
  page's address or field, a message, the arguments of an MCP tool, a step that uploads, posts or sends, and what runs
  code or types into an application (a command line, an AppleScript, keystrokes, a script run in a page with
  `browser_eval`, item 561), which may reach the network without saying so, are what it looks at (`policy/egress.py`),
  in the step's target, its summary and every string of its
  arguments, on every way a run acts (Dot's own loop, OpenCode, a batch of the browser or the desktop). The step is
  refused before it runs, and its `policy` event says why, with the secret masked. A command that reads a secret from a
  file or a variable as it runs (`curl -d @file`) carries none in its words, and is not seen here: the shell guard's
  flags (an upload, a read of a secret), the questions they raise or the taint check in yolo, and the sandbox are what
  stand there. A run you start with your own message is not marked, so it can still send a key where you tell it to.

**Secrets.**

- The memory tools refuse a note that holds a key-like string or one of the secrets Dot holds (`memory_save`,
  `memory_update`), and tell the model to record where the key lives, not the key. A note you write yourself, with
  `dotx memory add` or on the Memory page, is yours and not refused.
- What a run records of a step holds none of the secrets Dot holds (item 551), nor a key of a known form (a token, a
  private key, a password in an address or a header): the call's arguments (item 525) and the summary, the target and
  the text of its result, its `policy` event, its approval with the card and the push to your phone that ask about it,
  and the reason kept with its result. So do what the model says and thinks, as it streams and as it is stored, and the
  answer to a side question (`/btw`). The policy and the review model weigh the step as it is, and a card shows `***`
  where your own key stood in a command. What streams is masked as it comes, a secret split between two pieces of the
  stream included: what may still become part of a secret waits for the next piece (the word being written, the start
  of a key Dot holds, words such as `Bearer` or `password =` until their value has come, the header of a private
  key), and plain words are shown as they come. A secret in quotes, with spaces in it and introduced by more than a
  few words, can show its start while it streams; the stored message masks it whole.
- The browser and desktop tools check every field that would take a password, one-time-code or card text, whatever
  element the step names (a desktop `menu` step that pastes is not). Outside `yolo` they refuse it, and the step is
  yours (item 429), with the browser yours to take over and fill. In `yolo` the run types it (item 443): a code it can
  read, or a password you gave it, is its own. In the same way `terminal_send` types nothing, outside `yolo`, while
  the program's last line asks for a password, a passphrase or a PIN: there that step is yours, in a terminal of your
  own. The desktop tools never control a
  password manager or Keychain Access, in any mode, nor a window of Dot's outside `yolo`: the desktop app's, or one of
  another application (a browser, an installed web app) that shows the web UI, known at each step by the title the UI
  gives its page (its approvals, rules and settings decide what a run may do; a page of another site that takes such a
  title is refused as well). Desktop screenshots black out the password managers' windows, each known by its
  application's bundle id as well as its name, so on a Mac in any language (item 555); their secure fields are not
  masked.
- A login of your 1Password is filled by Dot, never by the run's model (item 493). Once you connect 1Password
  (Customize › Connectors › 1Password: a vault "Shared with Dot" and a service account that only reads it, or a service
  account token of yours), a run sees the logins' titles and websites (`browser_logins`) and names one with the refs
  of its fields (`browser_fill_login`); the daemon reads the values with `op`, 1Password's command line, and types them
  itself:
  - The values are in no argument of the model. Every result of the browser and web tools of the chat masks them
    from then on, for as long as Dot runs, as typed and as an address or a JSON string writes them: an error's words
    and context, a script's value, and the step's summary and target, which the approvals, the policy's events and
    the review model read (a form sent by GET puts them in the address). The judge of clicks and checks gets them
    masked too, in a button's words and a page's address, title and text. A screenshot of the browser covers the
    fields they were filled into, in every mode.
  - What is kept from the model is the password and the one-time code (your word of 2026-10-21). The username is no
    secret: a site shows it once you are signed in, and what the browser and web tools do not answer shows it as it
    is (the files a crawl writes, an MCP tool). The desktop tools are not wrapped: their screenshots show Dot's Chrome
    as the screen does, and they read Chrome's values of a plain field, a code there, or a password a page shows as
    plain text ("show password").
  - No script runs in the chat's tabs while the tab they were filled into is open, whatever page it went on to:
    going back can bring the filled page back whole. Once a script of the run ran in a tab of the chat, no tab of it
    gets a login until all of them are closed: the script could have left a listener that copies what is typed, in its
    page, in a tab it opened, or in a page Chrome brings back from its cache.
  - A login is filled only on a page of one of its own websites, or a host under one, on the port the website names
    (its scheme's own when it names none), over https (plain http on this Mac alone). Each field must be in a
    document of those sites, by the address the browser knows for its frame, which no script of the frame changes,
    so a frame of another site gets nothing. A password goes only into a password field, where it shows in no text.
  - No address holds a password or a code: an address is written into the views of the page, Chrome's history and
    Dot's log, and shortened where it is long. A form that names GET as its method, or a word no browser knows, gets
    neither. Dot's own Chrome refuses every request whose address carries one, as it is or once its escapes are read
    back (as UTF-8, and as a page of Western European text writes them), as a form that names no method sends it, or a
    script: a password for as long as Dot runs, a code for five minutes after it went in. The refusal and Dot's log
    name such a request by its host alone, or by its scheme when the value is in the host. A page in another legacy
    charset (Shift_JIS, KOI8-R and the like) can write a value Dot does not read back. Your own Chrome (Settings ›
    Browser › My Chrome) sends its requests unguarded, so there Dot fills a username alone; your password manager
    fills the rest. A page can still write them into its own words, which Dot masks where it shows them whole; only
    the login's own site, which has them anyway, can do that.
  - Each fill asks you first in every mode but `yolo`, whatever your rules say (only a rule that hands it off is
    stricter), and in `auto` you answer it, not the review model.
  - The token is kept in Dot's `.env`, which no tool of a run may read, under a name `op` does not read. The shell
    refuses `op` as a reading of a secret store.

  As every refusal of the review, that one is waived in `yolo`, which may read any file: the fill itself still shows
  the model no value.
- What a run reads of a web page never carries the value of a password, one-time-code or card field, outside `yolo`
  (item 467): the page's lines show `[hidden]` in its place, in the view of the page, in a search of it and in what
  changed after a step, and a screenshot covers the field in every frame, a payment provider's too. The fields are the
  ones Dot refuses to fill, by the page's marking (a `password` input, an `autocomplete` that names a secret); the
  field's name and ref stay, so it can still be found and filled. A value you type during a takeover, or one a page
  fills in, stays out of the model's conversation that way; typed from the screen that shows the page (item 470), it
  goes to the page and nowhere else: the daemon keeps how many clicks and scrolls there were and whether anything was
  typed, and a failure of the page is told without what you typed. A script of the run (`browser_eval`) could have
  left a listener in the page, or in a tab it opened, that copies what you type there, so taking over a chat in which
  one ran closes the chat's other tabs and opens the page again from its address first, a new document with no script
  of the run in it (item 554); a form the page was the answer of is not sent again, and the run is told in its next view.
  You act there, not Dot: nothing passes the policy,
  and in Dot's own Chrome the URL rules judge where your taps lead as they judge a run's; in your own Chrome (`attach`)
  they do not, as for a run's clicks. In `yolo` nothing is masked (item 443): the run reads all it is shown. A page
  script (`browser_eval`) reads a field's value whole, a value Chrome filled in from its password manager too, so
  outside `yolo` it is refused on a page that holds such a field, in any frame (item 533: use the other browser tools,
  which hide it, or take over), and from then on no text a browser tool of that chat gives back, no page text a judge
  reads and no line of Dot's log that names a refused address shows a value of the site's cookies (of the site, on any
  path), long enough to be a session and no plain word such as `dismissed`: it reads `[hidden]` in the script's value,
  in the error it threw, in the address it led the tab to, and in what a later tool reads of a page or an address a
  script wrote one into (also from a slice of the text, which is masked whole before it is cut). A script can still
  encode what it reads, and read what the page keeps in local storage; a screenshot shows the page as pixels, and a
  cookie a script wrote into the text of a page shows in it; the other lines of Dot's log that write an address write it
  as it is, a login's values among them; the live Screen view you watch is not masked.
  A key that pastes (`Control+V`, `Meta+V`, `Shift+Insert`) is refused in such a field as typed text is.
- The clipboard can hold a password you have just copied from your password manager. Outside a `yolo` run the sandbox
  refuses the clipboard's service to every process of a run (below), so no command, terminal session or AppleScript of a
  run can read it itself, whatever the line says; what another application does for a run is outside the sandbox (Apple
  Events, as below): Terminal's `do script`, a System Events keystroke that pastes into an application the run then
  reads back, a script in a file that does so; the Automation and Accessibility permissions you granted and the guard's
  names stand in the way of those. A run's tries are asked about in every preset (item 534), by the names the guard can
  read: `desktop_clipboard_read`; a shell line that names `pbpaste` (in any letter case, in any word of a wrapper's
  arguments such as `time -l pbpaste`, with the quotes the shell takes out: `grep pbpaste notes` asks as well); a line
  that runs `osascript` and names the clipboard anywhere (its `-e`, a pipe, a here-document, a variable); a line that
  names a pasteboard (inline code in another language: `python3 -c` with `NSPasteboard`); and any AppleScript or JXA
  script given to the tool that names the clipboard (`the clipboard`, `clipboard info`, their raw events,
  `theClipboard`, `clipboardInfo`, `pbpaste`, a pasteboard), whether it reads it or puts text on it: telling the two
  apart takes a reading of the script that a pattern gets wrong, and a string or a comment that names it asks too.
  `desktop_clipboard_write` hands nothing over and asks nothing more than before. A `yolo` run asks nothing and has no
  sandbox (item 443), and neither has a run with `[shell] sandbox = false`: there the guard's names are all there is,
  and what it does not read gets through (a name the line builds as it runs, a quote the shell takes out twice, `sh -c
  "pb''paste"`, a script in a file, `osascript r.applescript`, which asks as a script that may send (item 561) but not
  as a read of the clipboard, a script typed into a session, `osascript -i`).
- Environment variables whose names look like secrets (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL` or
  `AUTH` in the name) are removed from the processes the shell tool starts and from the `osascript` of the AppleScript
  tool, whose `do shell script` starts a shell with the same environment (item 555), `SSH_AUTH_SOCK` among them, so a
  command is not told where your SSH agent listens (`[shell] pass_env` lists exceptions). The agent's socket is a file
  in a fixed place under `/private/var/run`, which the sandbox does not close: a command that looks for it there still
  reaches the keys the agent holds unlocked.
- Outside a `yolo` run (which may, without asking, item 433) the agent can neither read nor change `~/.ssh`,
  `~/.gnupg`, `~/Library/Keychains`, `~/.aws`,
  `~/.config/gcloud`, the tokens and passwords that command lines keep (`~/.netrc`, `~/.git-credentials`,
  `~/.config/gh`, `~/.npmrc`, `~/.pypirc`, the credentials of Cargo and RubyGems, `~/.docker/config.json`, `~/.kube`,
  `~/.azure`, `~/.config/op`, `~/.password-store`), what 1Password, Bitwarden and Dashlane keep on this Mac, the keys
  that sign a Tauri app's updates (`~/.tauri`, the desktop app's among them) and the shell's history
  (`[policy] protected_paths`; a list of your own replaces this one), the data and caches of your browsers (Chrome and
  its Beta, Dev and Canary, Chromium, Safari, Firefox, Brave, Edge, Arc, Vivaldi and Opera), their cookies, Messages and
  Mail (`[policy] sealed_paths`, which the sandbox below closes to a command as well, item 531; a list of your own
  replaces this one too), nor Dot's own key file, token, what signing in to an MCP server gave it, browser profiles,
  configuration,
  database, the copies of the database (`[store] backup_dir`, and `<home>/backups-partial` while one is made, item
  526) and journals of file changes. The configuration holds the policy and the MCP servers, the database the rules,
  a copy all of the database and what you go back to, the journals what `dotx undo` writes back.
  For the locations of `[policy] protected_paths` this holds as far as the policy sees: the file tools refuse them, and
  the shell guard refuses a command line whose words reach one. What a script or a program reaches by itself is not
  seen (see "The shell guardrail is best-effort" below), and the sandbox closes only the sealed stores and Dot's own
  files to a command.
  In any mode, a tool that names files (the file tools, `web_extract`, `browser_upload`) looks at them again as it
  starts: a call whose path leads elsewhere by then (a link moved while the call waited for its approval) is refused,
  and called again it is decided for where the path leads now. A path is judged by the place it names (item 399):
  through a link, in another letter case, through the Data volume (`/System/Volumes/Data/Users/…`, which macOS joins to
  `/Users/…`) or behind `/.nofollow` or `/.resolve/<flags>`, `~/.ssh` is `~/.ssh`, for the file tools and for the words
  and globs of a command line; a file named by its number (`/.vol/<device>/<inode>`), which can be any file, is refused
  as a protected one.
- Outside a `yolo` run the agent reads what Dot loads and runs, and runs a skill's files, but changes them only once you
  agree: its own code (the folder its package is imported from, the Python environment it runs in and the Python under
  that, the file of the launchd job that starts the daemon, and the program that job starts it with: `Dot.app` in
  Dot's home and the folder it is built in, item 561), its plugins and their own files, its skills (with the
  `[skills] dirs` and `[plugins] dirs` folders, and the copies of the skills you added from other agents), Dot's records
  of the MCP servers you added from other agents and on Customize › Connectors, the home of the OpenCode server it starts,
  the playbooks it learned and the file of its emergency stop. The file tools ask you before they write there
  (`self_change`, item 398), in every approval mode but `yolo`, and neither the review model nor a rule of yours answers
  in your place; OpenCode's own agent, which writes past the journal `dotx undo` reads, is refused, and so is a line
  of the shell that writes there (a `yolo` run does both without asking, item 433): by a copy or a move (its target
  folder option too), `dd`, a redirect, a download, a
  clone, an unpacked archive, git run in one of those folders, a file edited in place (`sed -i`, `perl -i`, `ruby -i`)
  or inline code that names the place (in a word of switches such as `-pe` too). The guard reads the line, not what a
  program does; the sandbox below holds what it cannot read. `write_notes`, which asks nothing, writes its own file in
  the notes folder of the workspace and nothing else: a link at the folder or at the file, which such a line can make,
  is refused, never followed (item 556). A plugin's MCP servers, and those you added, start with the
  daemon and are given keys from the key store: put a plugin or a skill in place yourself, or install one from
  Customize › Plugins, which takes a gallery plugin's skills alone, as its commit pins them (item 542).
- Outside a `yolo` run the processes of a run start in a macOS sandbox (`sandbox-exec`; `[shell] sandbox`, on by
  default): a shell command, a terminal session, an AppleScript and what it runs can neither read nor change Dot's own
  files above (its key file, token, push key, MCP sign-ins, browser profiles, configuration, database and journals)
  and the stores no program has any use for (`[policy] sealed_paths`: the data and caches of your browsers, their
  cookies, Messages and Mail, item 531), nor change what Dot loads and runs, nor rename a folder above any of them,
  whatever program does it: a script the run wrote, code that builds the path, a symbolic link, another letter case.
  macOS answers `Operation not permitted`. A hard link is not closed, because the profile names paths: a hard link to
  one of those files that a process outside the sandbox made earlier (a `yolo` run, a run while
  `[shell] sandbox = false`) is another path to the same file, and a command reads it. The daemon
  says in its log, when it starts, which of the files above have another name on the Mac (`sealed_file_has_other_names`,
  item 561). The search tools may not read your protected locations either; a command still may
  (ssh and git read their keys there), and for those the guard reads its line. A search that meets a place it may not
  read shows
  what it found elsewhere and names the places it did not read, as a search of any run does with a folder whose reading
  macOS broke off (it does now and then under `~/Library/Containers` when the home folder is searched). macOS starts no
  program that runs as root inside the sandbox (`ps`, `top`, `sudo`, `crontab`): `pgrep -lf` and `lsof` work, a `yolo`
  run runs them, `[shell] sandbox = false` turns the sandbox off. Nor does a process of a run have another program act
  for it, outside the sandbox (item 418): it opens nothing through Launch Services (`open`, a program's own call),
  starts neither the `launchctl`, `shortcuts` nor `lsregister` that macOS installs and reaches neither the Shortcuts
  service nor the Launch Services database they would hand the work to, whatever program asks, creates no job of
  launchd, and writes nothing into `~/Library/LaunchAgents`, where launchd finds the jobs it starts at a login. Nor can it
  look up the clipboard's service (item 534: no `pbpaste`, no `the clipboard` of an AppleScript, no `NSPasteboard`, whatever
  the line says; `pbcopy` fails too, `desktop_clipboard_write` puts text there from Dot's own process). These
  rules are best-effort, as the guard's are. An application that is open is still driven (Mail, Calendar), with its own
  powers; `desktop_open_app` opens one from Dot's own process, and asks first for one whose bundle is not on the sealed
  system volume, however it names it: by the bundle's path, or by a name or a bundle id Launch Services resolves to
  one, which a run that registered a bundle it made can give it too. Settings › Rules switches it while the daemon
  runs ("Sandbox for a run's commands", `PUT /api/shell/sandbox`): from the next command on, with no restart, and the config
  file keeps it; a terminal session already open keeps how it started. Switching it on where `sandbox-exec` does not
  start is refused, and nothing changes.
- What Dot runs at its start is kept from a run's commands too: the login shell whose environment it reads runs its
  startup files, which a command may change, in the same sandbox, and the daemon's launchd job starts Python with `-P`,
  so a file a command left in Dot's home, the job's working directory, is never imported (run `dotx service install`
  again after an update to rewrite the job). The program the job runs is among what a command may not change: `Dot.app`
  in Dot's home, the app that starts the daemon, and the folder it is built in, are closed to a command's writes, new
  files and renames, so a command cannot put a program there and then end the daemon (launchd keeps its job and starts
  it again) to have it run outside the sandbox with your rights (item 561). Dot builds the app itself, from outside the
  sandbox, when `dotx service install` runs.
- What the sandbox does not cover: Apple Events, which macOS does not hold a `sandbox-exec` profile to (measured on
  macOS 26: with a rule over the Finder, the terminal apps, System Events and the rest, a script still drove the Finder
  and started an application that was not running), so an AppleScript can have another application act for it, with that
  application's powers: Mail sends a message the policy asked about, a terminal app runs a line outside the sandbox. The
  Automation permission you grant is what stands in its way, and the guard's reading of the script (what it reads of a
  script that drives Mail or Messages, and what it does not, is under *The guard* above, item 561). The desktop tools
  reach a terminal app through its keys, buttons and menus as well, which the guard judges as far as it reads them
  ([Browser and computer use](browser-and-computer-use.md#computer-use) says how far). Nor a program that speaks to
  launchd or another service through its own interface rather than the programs above, and the programs Dot starts itself (its browser, the MCP
  servers) run from where they are installed: a command can change the programs installed under your account
  (Homebrew's,
  the caches of npm and uv) and your shell's startup files, which those programs and your own Terminal then run. And it
  keeps files: a key saved with `dotx auth set` or in Settings is in the key file, which it seals, while a key kept in
  the keychain (service `dot-agent`) can be read by a command through `security`, in the sandbox too, and so can
  the tokens of the accounts of Google and Microsoft signed in (service `dot-oauth`, item 422).

**Typed judgments (optional, off by default).** The policy reads what a click does from the control's label, with
patterns in English and Turkish, and a few in Finnish ("Post", "Send", "Delete", "Pay now", "Paylaş", "Lähetä"…); a
label they do not know (Mastodon's "Boost", the German "Teilen") passes as an ordinary click. With `[judge] provider =
"typesafe"` (the switch "Typed judge (Jev)" on the page of TypeSafe in Settings › Providers writes it and switches the
judge from the next judgment on, with no restart; item 255) and `TYPESAFE_API_KEY` saved (made at console.typesafe.ai;
Settings › Providers, or `dotx auth set TYPESAFE_API_KEY`), TypeSafe's System One API is asked about such a click in
four yes/no questions (does it publish, send, pay or delete?) over the label, the role and the page's address (without
its query and fragment). A probability of at least `public_action_threshold` (0.45: below each yes measured; a wrong
flag only asks) gives the click that flag, so the policy treats it as a click on "Post" or "Delete", and the action's
summary says so: `(judge: public_post 0.93)`. The judge also reads a campaign's claim of its targets before the goal
checker does, and refuses a claim below `target_threshold` (0.15: only a clearly false one, since a true claim it
refused would keep the campaign from ending) at once. It never allows anything and never decides a step; it only adds
flags, or stops a claim early. With `[judge] second_opinion = true` (off by default) every question is asked a second
time, in a request of its own with an independent phrasing, and the two answers are read together: a click gets its flag
when either answer is at the threshold, and the summary names both and says when they disagreed (`judge: public_post
0.62/0.31 (disagreed)`), so a disagreement ends in the policy's question rather than in a silent pass; a claim is
refused only when both answers are below `target_threshold`, and a disagreement leaves it to the goal checker. It is a
paid service, though a cheap one (input tokens only, $0.042 a million on 2026-10-10): one request per judged click or
claim, two with a second opinion, at most `timeout_s` (5 s) each. The thresholds were set on answers of `jev-1.13.0`,
the version `[judge] model` names; move them together. A missing or refused key, a spent quota or a failed request turns
it off for `[models] helper_cooldown_s`, with a note in the timeline, and meanwhile the patterns and the goal checker
decide alone; a key saved in Settings turns it on again at once. `dotx doctor` shows whether it is on and has its key.
The click questions are asked in every mode, `yolo` too, where the payment, message or post they find is the run's
(item 443) and the step's record keeps the flags they raised
(item 429). Two more checks run before a model call when the judge is on. A `browser_act` that names what it expects to
see after its steps (`expect`: "the reply shows under the post") gets, above its result, the probability that the page's
text shows it and that the page is a login wall, a CAPTCHA or an error page, from one request over the page's visible
text, so the next turn starts from those numbers instead of reading the whole page. And the goal check of a run in goal
mode asks the judge first, as a campaign's claim does: an answer whose claim of success scores below `target_threshold`
is sent back with the judge's number and without the review model's call; a clear yes and the uncertain middle go to the
review model as before.

**A step's model chosen by a typed judgment (off by default).** With `[agent] step_router = true` and
`TYPESAFE_API_KEY` saved, every step of a run starts with one Choice of TypeSafe's over the run's models, from the goal,
what the last step brought back, the failures so far and the failures in a row: a routine step goes to `[models] fast`,
a hard one to the first available helper of `[models] helpers`, and the run's own model takes a step unless another
model's probability reaches `step_router_threshold` (0.6). A model that cannot take the run's tools, its window or its
images is not offered; a routed model that cannot answer has the step asked again on the run's model. Each step another
model takes is a `step_routed` line under Set up in the side panel's Run tab (item 365). It stays off until a comparison
on your runs shows less time per task at the same success: a change of model loses the prompt cache the hosted providers
keep, and a weaker model can add steps.

Whatever `[judge] provider` says, saving `TYPESAFE_API_KEY` also gives the agent the `judge_items` tool (acting and
research runs; the daemon reads the key when it starts): one question about every item of a list, answered in one
request and handed back ranked, so the model reads a short ordered list instead of weighing each search hit, post, link
or file in its own words. Without levels every item gets the probability of yes to a yes/no question ("Is this post
about Python packaging?"); with two to ten ordered levels every item gets its expected level and the likeliest one.
Each item's question is its own, so the ids stay in the code and the question carries the whole meaning; a list longer
than `items_per_request` (100) goes in several requests, each bounded by `timeout_s`. The ranking is a probability, not
a fact: the decision stays with the model. The tool and the judge share one connection and one pause: an outage pauses
both for `helper_cooldown_s`; a request TypeSafe rejects (HTTP 422) or an answer outside its reference is an error of
Dot's code or of the model, logged with the request and shown in the timeline as `judge_error`, and pauses nothing.

**Pace on social platforms.** A click whose label posts, replies, reposts, quotes or sends ("Post", "Reply", "Repost",
"Quote", "Yeniden gönder", "Lähetä"…) is a public action; one that follows, likes or subscribes ("Follow", "Like",
"Takip et", "Beğen", "Seuraa", "Tykkää"…) is a social action, of low risk, which asks once the run has read untrusted
content. Both are counted on their site (the page's host, without `www.`), across runs and threads, in a sliding hour
and day, and nothing limits them by default: Dot posts, follows and likes as often as its task needs. `[policy.pace]`
sets limits (`public_per_hour`, `public_per_day`, `social_per_hour`, `social_per_day`, and `public_spacing_s` between
public actions, which the browser waits out; 0 removes a limit). An action past a limit that is set is refused in every
approval mode, `yolo` too, with the time the next one is allowed, and the timeline shows the refusal. The actions of
two runs on one site are judged one after the other, so the second counts the first. While a limit of public or
social actions is set, a `browser_act` call takes one public or social click at most, so that each one counts; with
none set, as by default, a call takes as many as its steps (item 266). Only the clicks the policy recognises are
counted: a post sent with a keyboard shortcut, by a script in the page (`browser_eval`) or through an API from the
shell is not. X publishes limits of its own (an
unverified account: 50 posts and 200 replies a day, 400 follows, 500 messages), and its automation rules forbid
automated likes and bulk or aggressive following, and warn that scripting its website can get an account suspended.

**Approvals.** An action that needs a yes becomes an approval: `allow? [y/N]` in the terminal, a card in the web UI. The
run waits (its status is `blocked`) for up to `[policy] approval_timeout_s`; an approval nobody answers expires and
counts as a refusal. With `[policy] unanswered = "park"` a hand-off nobody answers is kept for you instead and the run
goes on without it (item 482, [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)); your **I did
it** on it gives the run no authority, and the words it sends restore no trust. A question whose card shows all of a
step that reaches nobody (a command, a file read or a folder listing, a page read or a web search; never one the run's
untrusted reading raised, nor one about Dot's own files) is kept too, and your **Allow once** lets the run do that exact
step once in that conversation, within `[policy] permit_hours`, after the policy decided it again (item 483). The note
you write with your answer reaches the model. Your no stands for the rest of the run (item 456): the same action, as its
card showed it, is refused at once, with no new card, nor a question to the review model in `auto`, until you write to
the run again (a steer, the answer to its question); the policy's event records the refusal, as it does the first. Only
your no is kept, given in the web UI or the terminal: a refusal of the review model, of the eval harness and an approval
nobody answered are not; and an action whose card shows less than all of it is asked again: a kind whose card says not
what it writes or types (a file's text, a field's), and a card that cut its text (`…`, an MCP call's long arguments
among them), told a text by its length (`(42 chars)`) or masked a secret (`***`), since two actions such a card cannot
tell apart are not one. This is the default approval mode, `ask`, and `supervised` asks before every change as well; in
`auto` the questions of the policy are answered for you, and a `yolo` run asks nothing at all (item 443). In every mode
that asks (item 429), money and credentials are handed to you on a card in the chat, and a message or a post in your
name waits there for your yes in `supervised` and `ask`, for the review model's in `auto`.
A question of the agent itself (`ask_user`) waits the same way when the run cannot go on without the answer; one it can
go on without waits on the chat while the run works, and one in a run nobody can answer during it is left for you (see
[Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)). Its options are rows on the card, each with
what it leads to, and the one Dot recommends is marked: a row sends its option, or, when several may be chosen, the rows
ticked are sent with what you write, as one answer. In the terminal you type an option's number (several, separated by
commas) or your own words. The run reads which option you chose, and whether it was the one it recommended. A line
typed in the terminal before a prompt for a decision appears (the next message of a chat, typed while the run worked)
is set aside and answers nothing, and the prompt says so; at a hand-off only Enter or a yes says that you did the step,
and any other answer skips it (item 558).

**Messages from a channel** (item 572, [Channels](channels.md)). With iMessage on (Settings › Channels), a message you
send to yourself starts a run as one typed in the web UI does: it is yours and trusted, so the run starts untainted, in
the mode and with the approvals its chat keeps (Auto and `[policy] approvals` until you choose others in the chat's
composer). What makes it yours is where it came from: a row of Messages' database that Messages flags as sent by you
over iMessage, in a one-to-one conversation named for a number or an address you paired with a code typed back. A
message from anyone else, in a group, by text message or as a reaction is never read as a command; Dot's own messages
(its answers, and what it tells you on its own, item 582, whose words may be a third party's: a mail's subject) land in
the same conversation and each starts with `◆`, which is how they are told from yours. Nobody without your Apple ID can
write such a row from outside this Mac, but a run of Dot can from inside it: a message it sends through
AppleScript, through keystrokes in Messages or through a tool of an MCP server lands in that conversation flagged as
yours, and Messages' database cannot tell it from yours. The guard asks before such a send, but in `auto` the review
model answers, not you, and in `yolo` nobody does, so a run that read untrusted content could write itself a command and
have it read as yours, untainted. The channel does not run it, in any mode: a row dated in the window of a step of any
run that raised `message_send` (from five minutes before the step began, its wait for an answer included, to ten seconds
after its end; a step that sends and goes on stays in its window until it ends, about 33 minutes at the most) is told
and not run, and so is a message Messages sends later than it was written (Send Later of macOS 15), and a draft to a
number or address you paired is refused. What this does not catch is a send the guard cannot see, which raises no flag:
a script of a page, a program the run wrote and then runs, a Shortcut, a job a step left behind. `/stop` is run in any
case. Pair only your own number or Apple ID address: the code you type back shows only that it reached whoever holds the
number, so with someone else's every message you send them would be read as your command. Approvals and questions are
never answered on a channel: a line says one waits and where to answer, in Dot's UI or the phone app, so that a
channel's account, if taken, could not approve anything. A message is read up to 4,000 characters and 30 a minute, and
one that came more than 15 minutes before Dot read it is not run: Dot says so. A row that is not words (a sticker, an
app's message, a payment: rich content, which Messages marks with a bundle of its own) is never run, and neither is a
row that is only a link that Dot wrote, one into its own pages or one in a message it sent a minute before: Messages may
split the link at the end of a message of Dot's into a row of its own, which has no `◆` and is flagged as yours, and it
would else start a run whose answer might end with a link as well. A link of yours that Messages splits off your words,
less than 3 seconds after them, is joined to them as one command.

**The record of each step.** Every step that the policy let through keeps, with its result, what decided it (item 468):
the verdict, where it came from (one of your rules, named, the preset, the guard, the taint, supervised...), the reason,
the preset of the run, who answered when there was a question: nobody (the policy let it through), you, the review
model, or yolo, which asks nothing; whether the run was tainted when the gate decided (item 474); and the flags its
action raised (money, a credential, a message in your name...). Since item 474, a step of an OpenCode agent keeps the
same. A step refused or handed to you has its `policy` event instead, which names the rule and keeps the taint too. The
steps a browser batch takes inside one call are the batch's one record; one of them that asked has its own `policy`
event and approval.

**Emergency stop.** Any of these stops Dot from controlling the desktop:

- the **Emergency stop** button of the web UI (Settings › System; or `POST /api/failsafe`; a chat's own run stops with
  the stop button of its composer) creates the `STOP` file in the home
  directory and ends every run as an emergency stop, a run that was starting at that moment too (a campaign whose
  session it ends stops too), and the run of a `dotx` command that works in its own terminal: the command asks its
  process to end it so, which it does within a second (item 553); the page shows a banner with a button that releases it
- `touch ~/Library/Application\ Support/Dot/STOP` blocks desktop control the same way, without ending a run; delete the
  file to allow it again
- while the `STOP` file exists, no schedule fires (a firing due then is skipped to its next time, saying why; a
  one-time schedule, which has no next time, waits and fires once the stop is released) and no campaign starts a
  session (it starts once the stop is released); a run that a schedule or a campaign was starting when the file
  appeared ends as an emergency stop as soon as it exists
- with `[desktop] failsafe_corner = true` (the default), moving the pointer into the top-left corner of the screen
  aborts the desktop step in progress, and Dot never clicks that corner
- before each step of a desktop batch Dot checks that the pointer is where the previous step left it; if someone else is
  moving the mouse, the rest of the batch is not run

> **The shell guardrail is best-effort; the sandbox holds only Dot's own files and what would act outside it.** Dot
> reads a shell command before it runs it and judges what it would touch: it reads the line as the shell does (quotes,
> comments, here-documents, the commands substituted into it, a `case` inside one, a `cd` however it is spelt, a
> variable, a link it makes, a wrapper such as `bash -c`, `env`, `xargs`, `xcrun` or `uv run`, a `trap`, the settings
> and variables that make git or another program run a command line), compares paths, and the names of commands, without
> case (`RM -rf`, `SUDO` and `CURL … | SH` run the commands of the lower-case names on this Mac's file system, item
> 538), and treats what it cannot read (a command named by a substitution or a pattern, `eval` of one, a setting of git
> it does not know, a line it cannot parse) as a destructive step outside the workspace, which every preset asks about.
> A script the line downloads and then runs (`curl -o`, `wget -O` or `git clone`, then a shell, `source`, its path or an
> interpreter such as `python3`, also after a `cp`, an `mv` or a `ditto`, or on standard input: `sh < y.sh`, `cat y.sh |
> bash`) is judged as piping the download into a shell; a file named after an at sign (`curl --data-binary @file`) as
> that file; a script handed to a terminal app (`open -a Terminal`, a `.command` file) as a step outside Dot's sandbox,
> which every preset asks about (item 385), and outside yolo the sandbox refuses the opening itself (item 418). A word
> with a part only the running line knows (`~/.s$(echo sh)/id_rsa`, `~/Library/${X:-Safari}/History.db`, a variable
> filled with what a command prints) that begins as a protected location's path, or ends with the last names of one
> (`~/Documents/$(echo ..)/.ssh/id_rsa`), or climbs (`..`) beside a part it does not know (`$(pwd)/../.ssh/id_rsa`; the
> climb alone of a `cd` is no step), or is a word with more spellings, by variables and braces, than it reads (64; a
> program or a literal in quotes as dense as that asks too, since the guard cannot tell its braces from ones the shell
> spells out), and a command that reads every file under a folder that holds one (`grep -r secret ~`, `rg token
> ~/Library`, `find ~ -exec cat {} \;`, `find ~ | xargs cat` or `cat $(find ~ -name id_rsa)`, `cp -R`, `ditto`, `rsync
> -a`, `scp -r`, `tar` creating an archive or adding to one, `zip -r`, `diff -r`, `git grep` and `git diff --no-index`,
> `hdiutil create -srcfolder`, `rclone`, `aws s3 sync`, `gsutil`, `pax`, and what reads the names of a listing from its
> standard input: `cpio`, `tar -T -`, `zip -@`; a folder given as a glob that matches a protected location or one above
> it, `~/Lib*` and `/Users/*`, counts as that folder, and the GNU tools Homebrew installs as `ggrep` or `gfind` as the
> tools they are) are asked about in every preset (item 531), as a read that may reach a protected location. A command
> that runs in another folder by an option of its own (`env -C`, `git -C`, `tar -C`, `make -C`, `uv --directory`, `yarn
> --cwd`, `pnpm --dir`) has its words judged from there too (a line that moves between more than four different folders
> asks, each being read against every word): `git -C ~ diff --no-index .ssh/id_rsa /dev/null` names the key of `~/.ssh`.
> A recursive search reads the folder the line is in when it is given none, and which of its words is a folder is not
> told without each tool's options, so from a folder that holds a protected location (the home folder) it asks whatever
> folder it is given. It does not follow what a recursive command does inside the other folders it walks, nor the code
> of a script it runs, nor `git add -A` in a folder that holds one, nor the other tools that upload or copy a folder
> whole under names it does not know, nor a loop that reads what a search lists (`find ~ | while read f; do cat "$f";
> done`), nor a path whose unknown part holds the protected folder's own name (`~/Documents/$(echo ../.ssh)/id_rsa`);
> the sandbox keeps those out of Dot's own files and of the sealed stores (the data of your browsers, Mail and
> Messages), not out of the rest of yours, and a shell can do more than any analysis can foresee. Treat Dot as a user of
> your account with the rights of your account: keep backups, keep the workspace for scratch work, and use `balanced` or
> `cautious` on a machine where a mistake would be expensive.
