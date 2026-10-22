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
  OpenRouter passes each call on to the lab whose model it is (see
  [Models and providers](models.md#models-and-providers)).
- **To the embedding model: each note and each search of the memory.** It is Ollama's `bge-m3` on this Mac by default;
  with `[memory] embed_provider = "openai"` the texts go to OpenAI (see [Memory](memory-and-schedules.md#memory)).
- **To the search engines: the query.** The `[web] search_backend` ladder asks the owner's SearXNG instance when
  `[web] searxng_url` names one (the instance passes the query on to its own engines, Google and the others), then
  Ollama's hosted search when `OLLAMA_API_KEY` is saved, then DuckDuckGo and Brave Search, then DuckDuckGo in Dot's own
  Chrome; Anthropic's search (relayed by `[models] search`) joins when `ANTHROPIC_API_KEY` is saved, and
  `[web] anthropic_search = "off"` leaves it out.
- **To the sites a run visits: its requests.** The web tools fetch public addresses only, unless
  `[web] allow_private_hosts` allows more (the owner's SearXNG instance is configuration and is reached wherever it
  is, a loopback one too); Dot's browser carries the cookies of its profile (Dot's own, or your Chrome's
  when it is attached). `web_extract` sends what it read to the `fast` model, and a page a run shows in the conversation
  loads its libraries from public CDNs (see [Tools](runs.md#tools)).
- **From the shell: whatever a command sends.** The sandbox closes Dot's own files, not the network; a run that has read
  untrusted content asks before a line that sends data out (see [Safety](#safety)).
- **To TypeSafe, once its key is saved: the texts it judges.** `judge_items` and the tools of posts and campaigns send
  the texts they judge; with `[judge] provider = "typesafe"` the judge of clicks and checks sends what it judges (a
  button's words, the page's address without its query, its title and visible text). `[judge] provider` is `"none"` by
  default.
- **To a phone, once you turn its notifications on: a line.** A title, up to 140 characters of the question, of a
  draft's recipients and subject (a message's words, item 314) or of the result, and the page to open, encrypted for
  that phone's browser and carried by its maker's push service (see [Dot on the phone](phone.md#dot-on-the-phone)).
- **To MCP servers and plugins: what their tools are given.** A server's own process reaches what it reaches; none is
  set up by default (see [MCP servers](extending.md#mcp-servers)).

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
payment), so that a misread errs towards the hand-off; under both caps the step is a
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
   that pays is the one handed to you (item 522).
5. *The guard* (item 429): a message sent for you (`message_send`: an email, a chat or text message, a reply or a
   forward, through AppleScript, a messaging app, a Send button, an MCP tool whose name says it sends or posts, or a
   mailer of the command line) and a post in your name (`public_post`) wait for your yes, in every mode that asks,
   whatever your rules say; only a rule that hands the step to you is stricter. A rule that allows it does not answer for
   you. A login filled from your 1Password (`vault_fill`, item 493, below) is guarded the same way, and in `auto` you
   answer it, not the review model. A `yolo` run asks nothing and makes the step, its record keeping the reason it would
   have asked for (item 443).
   What the policy cannot tell for a message is not guarded: a key that sends in a web page's chat box, a page's own script
   (`browser_eval`), a program of the shell that talks to a mail server or a chat's API itself (`curl`, a script).
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
   move it one place up or down that order, and **New rule** opens the two ways to write one. A rule that an earlier
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
   word (`x.com` does not fit `netflix.com`). The draft fills the form, its description the sentence the rule reads
   back as ("For “git status” run alone in the shell, …: Dot does it without asking."), and applies once you add it.
   Words that cover every action ("Dot can run everything without asking me") draft a rule for every tool ("For every
   use of any tool: Dot does it without asking."), which the protections that are always on still bound; words that
   make no rule are answered with the reason, in English.
   An approval card offers *Make a rule of this*, which opens the tab with a sentence about the action in the box.
7. *The preset* decides everything that is left; in the approval mode `supervised` an action it allows still asks you
   when it changes something (see [Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)):

| Preset                 | Asks before                                                                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `aggressive` (default) | privilege, system settings, public posts, messages sent for you, destructive actions outside the workspace, and a read that may reach a protected location or the clipboard |
| `balanced`             | all of that, plus anything destructive or installing, anything of high risk, and writing or shell commands outside the workspace                                            |
| `cautious`             | everything that is not read-only                                                                                                                                            |

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
failed `edit_file` of one names none of its lines. A run that has seen such content is marked untrusted, and from then
on outward-facing or lasting actions (messages, posts, follows and likes, privilege, system settings, saving a schedule
or a note, installing software (`brew install`, `pip install`, `npm install -g`, `cargo install`, `uv tool install`) or
running a script the line downloaded (item 523), or a blob it decoded and ran (`base64 -d x | sh`, or a decode to a
file the line then runs; item 528), and a shell line that sends data out: an upload or a form, a push, a
copy to another machine, a change on a code host, inline code that reaches the network or runs a command of it, and a
read that may reach a protected location, item 531) ask even when the preset or an `allow` rule would let them through;
only a `preapproved` rule skips the question. A
project's own install (`npm install`, `uv sync`, `uv add`) goes ahead. A message you send to the run clears the mark;
your answer to a question of a run you watch does not, whether the run waited for it or went on (item 508), as it
answers what the run asked rather than sending it on its way: the run it reaches stays marked, and the run it starts
once that run has ended starts as untrusted as that run ended. A delegated run starts as untrusted as its parent, a
resumed run as the run it resumes was when it was cut off, and the run that tells a chat that its command in the
background ended as the chat's last run ended: that message is Dot's, not yours, and so is the note that tells a run at
work of such an end, which clears nothing. A message you send starts a run unmarked. A note a run saved while it was
marked keeps that origin (`tainted`): it is never among the notes a run starts with, is handed while it works or as a
site's lessons (item 384), a `memory_search` that returns one marks the run that read it, so its next save asks again,
and Settings › Memory and `dotx memory list` say of such a note that it was saved while reading outside content.

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
  code or types into an application (a command line, an AppleScript, keystrokes), which may reach the network without
  saying so, are what it looks at (`policy/egress.py`), in the step's target, its summary and every string of its
  arguments, on every way a run acts (Dot's own loop, OpenCode, a batch of the browser or the desktop). The step is
  refused before it runs, and its `policy` event says why, with the secret masked. A command that reads a secret from a
  file or a variable as it runs (`curl -d @file`) carries none in its words, and is not seen here: the shell guard's
  flags (an upload, a read of a secret), the questions they raise or the taint check in yolo, and the sandbox are what
  stand there. A run you start with your own message is not marked, so it can still send a key where you tell it to.

**Secrets.**

- The memory tools refuse a note that holds a key-like string or one of the secrets Dot holds (`memory_save`,
  `memory_update`), and tell the model to record where the key lives, not the key. A note you write yourself, with
  `dotx memory add` or on the Memory page, is yours and not refused.
- The browser and desktop tools check every field that would take a password, one-time-code or card text, whatever
  element the step names (a desktop `menu` step that pastes is not). Outside `yolo` they refuse it, and the step is
  yours (item 429), with the browser yours to take over and fill. In `yolo` the run types it (item 443): a code it can
  read, or a password you gave it, is its own. In the same way `terminal_send` types nothing, outside `yolo`, while
  the program's last line asks for a password, a passphrase or a PIN: there that step is yours, in a terminal of your
  own. The desktop tools never control a
  password manager or Keychain Access, in any mode, nor a window of Dot's outside `yolo`: the desktop app's, or one of
  another application (a browser, an installed web app) that shows the web UI, known at each step by the title the UI
  gives its page (its approvals, rules and settings decide what a run may do; a page of another site that takes such a
  title is refused as well). Desktop screenshots black out the password managers' windows; their secure fields are not
  masked.
- A login of your 1Password is filled by Dot, never by the run's model (item 493). Once you connect 1Password
  (Customize › Plugins › 1Password: a vault "Shared with Dot" and a service account that only reads it, or a service
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
  typed, and a failure of the page is told without what you typed. You act there, not Dot: nothing passes the policy,
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
  "pb''paste"`, a script in a file, `osascript r.applescript`, a script typed into a session, `osascript -i`).
- Environment variables whose names look like secrets (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL` or
  `AUTH` in the name) are removed from the processes the shell tool starts, `SSH_AUTH_SOCK` among them, so commands do
  not reach the keys of your SSH agent (`[shell] pass_env` lists exceptions).
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
  In any mode, a tool that names files (the file tools, `web_extract`, `browser_upload`) looks at them again as it
  starts: a call whose path leads elsewhere by then (a link moved while the call waited for its approval) is refused,
  and called again it is decided for where the path leads now. A path is judged by the place it names (item 399):
  through a link, in another letter case, through the Data volume (`/System/Volumes/Data/Users/…`, which macOS joins to
  `/Users/…`) or behind `/.nofollow` or `/.resolve/<flags>`, `~/.ssh` is `~/.ssh`, for the file tools and for the words
  and globs of a command line; a file named by its number (`/.vol/<device>/<inode>`), which can be any file, is refused
  as a protected one.
- Outside a `yolo` run the agent reads what Dot loads and runs, and runs a skill's files, but changes them only once you
  agree: its own code (the folder its package is imported from, the Python environment it runs in and the Python under
  that, and the file of the launchd job that starts the daemon), its plugins and their own files, its skills (with the
  `[skills] dirs` and `[plugins] dirs` folders, and the copies of the skills you added from other agents), Dot's records
  of the MCP servers you added from other agents and on Customize › Plugins, the home of the OpenCode server it starts,
  the playbooks it learned and the file of its emergency stop. The file tools ask you before they write there
  (`self_change`, item 398), in every approval mode but `yolo`, and neither the review model nor a rule of yours answers
  in your place; OpenCode's own agent, which writes past the journal `dotx undo` reads, is refused, and so is a line
  of the shell that writes there (a `yolo` run does both without asking, item 433): by a copy or a move (its target
  folder option too), `dd`, a redirect, a download, a
  clone, an unpacked archive, git run in one of those folders, a file edited in place (`sed -i`, `perl -i`, `ruby -i`)
  or inline code that names the place (in a word of switches such as `-pe` too). The guard reads the line, not what a
  program does; the sandbox below holds what it cannot read. A plugin's MCP servers, and those you added, start with the
  daemon and are given keys from the key store: put a plugin or a skill in place yourself.
- Outside a `yolo` run the processes of a run start in a macOS sandbox (`sandbox-exec`; `[shell] sandbox`, on by
  default): a shell command, a terminal session, an AppleScript and what it runs can neither read nor change Dot's own
  files above (its key file, token, push key, MCP sign-ins, browser profiles, configuration, database and journals)
  and the stores no program has any use for (`[policy] sealed_paths`: the data and caches of your browsers, their
  cookies, Messages and Mail, item 531), nor change what Dot loads and runs, nor rename a folder above any of them,
  whatever program does it: a script the run wrote, code that builds the path, a link, another letter case. macOS
  answers `Operation not permitted`. The search tools may not read your protected locations either; a command still may
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
  again after an update to rewrite the job).
- What the sandbox does not cover: Apple Events, which macOS does not hold a `sandbox-exec` profile to (measured on
  macOS 26: with a rule over the Finder, the terminal apps, System Events and the rest, a script still drove the Finder
  and started an application that was not running), so an AppleScript can have another application act for it, with that
  application's powers: Mail sends a message the policy asked about, a terminal app runs a line outside the sandbox. The
  Automation permission you grant is what stands in its way. Nor a program that speaks to launchd or another service
  through its own interface rather than the programs above, and the programs Dot starts itself (its browser, the MCP
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
`auto` the questions of the policy are answered for you, and a `yolo` run asks nothing at all (item 443). What is yours
in every mode that asks (item 429) — money, credentials, messages and posts in your name — is a card in the chat there.
A question of the agent itself (`ask_user`) waits the same way when the run cannot go on without the answer; one it can
go on without waits on the chat while the run works, and one in a run nobody can answer during it is left for you (see
[Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)). Its options are rows on the card, each with
what it leads to, and the one Dot recommends is marked: a row sends its option, or, when several may be chosen, the rows
ticked are sent with what you write, as one answer. In the terminal you type an option's number (several, separated by
commas) or your own words. The run reads which option you chose, and whether it was the one it recommended.

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
  session it ends stops too); the page shows a banner with a button that releases it
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
> and variables that make git or another program run a command line), compares paths without case, and treats what it
> cannot read (a command named by a substitution or a pattern, `eval` of one, a setting of git it does not know, a line
> it cannot parse) as a destructive step outside the workspace, which every preset asks about. A script the line
> downloads and then runs (`curl -o`, `wget -O` or `git clone`, then a shell, `source`, its path or an interpreter such
> as `python3`, also after a `cp`, an `mv` or a `ditto`, or on standard input: `sh < y.sh`, `cat y.sh | bash`) is judged
> as piping the download into a shell; a file named after an at sign (`curl --data-binary @file`) as that file; a script
> handed to a terminal app (`open -a Terminal`, a `.command` file) as a step outside Dot's sandbox, which every preset
> asks about (item 385), and outside yolo the sandbox refuses the opening itself (item 418). A word with a part only the
> running line knows (`~/.s$(echo sh)/id_rsa`, `~/Library/${X:-Safari}/History.db`, a variable filled with what a
> command prints) that begins as a protected location's path, or ends with the last names of one (`~/Documents/$(echo
> ..)/.ssh/id_rsa`), or climbs (`..`) beside a part it does not know (`$(pwd)/../.ssh/id_rsa`; the climb alone of a `cd`
> is no step), or is a word with more spellings, by variables and braces, than it reads (64; a program or a literal in
> quotes as dense as that asks too, since the guard cannot tell its braces from ones the shell spells out), and a
> command that reads every file under a folder that holds one (`grep -r secret ~`, `rg token ~/Library`, `find ~ -exec
> cat {} \;`, `find ~ | xargs cat` or `cat $(find ~ -name id_rsa)`, `cp -R`, `ditto`, `rsync -a`, `scp -r`, `tar`
> creating an archive or adding to one, `zip -r`, `diff -r`, `git grep` and `git diff --no-index`, `hdiutil create
> -srcfolder`, `rclone`, `aws s3 sync`, `gsutil`, `pax`, and what reads the names of a listing from its standard input:
> `cpio`, `tar -T -`, `zip -@`; a folder given as a glob that matches a protected location or one above it, `~/Lib*` and
> `/Users/*`, counts as that folder, and the GNU tools Homebrew installs as `ggrep` or `gfind` as the tools they are)
> are asked about in every preset (item 531), as a read that may reach a protected location. A command that runs in
> another folder by an option of its own (`env -C`, `git -C`, `tar -C`, `make -C`, `uv --directory`, `yarn --cwd`, `pnpm
> --dir`) has its words judged from there too (a line that moves between more than four different folders asks, each
> being read against every word): `git -C ~ diff --no-index .ssh/id_rsa /dev/null` names the key of `~/.ssh`. A
> recursive search reads the folder the line is in when it is given none, and which of its words is a folder is not told
> without each tool's options, so from a folder that holds a protected location (the home folder) it asks whatever
> folder it is given. It does not follow what a recursive command does inside the other folders it walks, nor the code
> of a script it runs, nor `git add -A` in a folder that holds one, nor the other tools that upload or copy a folder
> whole under names it does not know, nor a loop that reads what a search lists (`find ~ | while read f; do cat "$f";
> done`), nor a path whose unknown part holds the protected folder's own name (`~/Documents/$(echo ../.ssh)/id_rsa`);
> the sandbox keeps those out of Dot's own files and of the sealed stores (the data of your browsers, Mail and
> Messages), not out of the rest of yours, and a shell can do more than any analysis can foresee. Treat Dot as a user of
> your account with the rights of your account: keep backups, keep the workspace for scratch work, and use `balanced` or
> `cautious` on a machine where a mistake would be expensive.
