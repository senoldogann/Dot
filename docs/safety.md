# Safety and privacy

## What leaves this Mac

Dot sends nothing to the people who make it: there is no telemetry, no crash report and no check for updates. What
leaves the Mac is what a run needs, sent where the configuration points:

- **To the models: the run's conversation.** Each call sends the goal, the messages, what the tools returned (the text
  of the files and pages the run read, what commands printed) and the screenshots the run takes or is given. By default
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
into a field is yours in every mode, `yolo` too (item 427): Dot hands the field to you.

**Pre-approved spending.** `[policy.spend]` lists sites with caps, for example `"namecheap.com" = { per_purchase = 20,
per_day = 50 }`. Dot reads the amount of a payment step from the labels of its controls ("Pay €19.90") and from the
total on the page, and takes the largest (item 385: a coupon or a basket line in the same step cannot stand for the
payment), so that a misread errs towards the hand-off; under both caps the step is a
question instead of a hand-off (`auto` lets the review model answer, `ask` and `yolo` ask you once), it
is recorded with its amount, shown in the timeline, and counts against the site's day.
The payments of a site are decided one at a time, each from what the ones before it spent, so two runs paying at once
cannot both take what is left of the day. Over a cap, on a site not listed, or when the page does not show the amount,
the money hand-off holds as before. The caps are plain numbers compared with the number on the page, whatever its
currency, so list sites that bill in one currency. Empty by default.

**Decision order**, strictest first:

1. *Auto-review* denies actions that read secrets or send data out (`secret_access`, `exfiltration`), whatever else is
   configured; a `yolo` run asks you instead (item 427).
2. The *run mode* denies what a research run may not do.
3. *Dot's own files*: a change of them (`self_change`, below) is a question to you in every approval mode but `yolo`,
   which no rule of yours and no review model answers in your place (item 398). Allowed, the run makes the change.
4. *Hard stops* hand money and credentials to you (`financial`, `credential`): Dot stops and asks you to do that step
   yourself. A payment under the pre-approved caps of its site (above) is a question instead of a hand-off, unless
   one of your rules hands that step to you (item 385): the caps stand in for the money hand-off, not for your rules.
5. *The guard* (item 427): a message sent for you (`message_send`: an email, a chat or text message, a reply or a
   forward, through AppleScript, a messaging app, a Send button, an MCP tool whose name says it sends or posts, or a
   mailer of the command line) and a post in your name (`public_post`) wait for your yes, in every mode, whatever your
   rules say; only a rule that hands the step to you is stricter. A rule that allows it does not answer for you.
6. *Your rules* come next. The first enabled rule, in order, whose tool pattern and regular expression match the action
   decides, with one of four answers: *do it* (`allow`), *do it, whatever it has read* (`preapproved`: the question
   untrusted content raises is skipped too), *ask me first* (`ask`) or *hand it to me* (`handoff`: Dot stops, says what
   it needs done and waits; on a browser step you can take the browser over, do it and give it back; when you say you
   did it, the run goes on from there). The expression is searched, case-insensitive, in the action's one-line summary
   (a shell command's starts with `Run: `), a line break and its target (the command, the path, the address); one that
   takes longer than 0.25 s to match is stopped and the action refused, as for a rule that cannot be read, so that a
   pattern that backtracks for long cannot hold the daemon (item 387). A rule
   reads as a sentence in Settings › Rules, "When Dot would use `shell`, matching `\bgit push\b`: ask me first", and
   is managed there or through `/api/rules`; the page lists the rules in the order they are checked and **New rule**
   opens the two ways to write one. It can also be said in your own words ("let Dot run git status and git
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

| Preset                 | Asks before                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `aggressive` (default) | privilege, system settings, public posts, messages sent for you, and destructive actions outside the workspace              |
| `balanced`             | all of that, plus anything destructive or installing, anything of high risk, and writing or shell commands outside the workspace |
| `cautious`             | everything that is not read-only                                                                                            |

A shell line is destructive outside the workspace when it removes, cuts or moves away a file there (item 385: `mv` takes
its sources from where they were), and when it replaces one that is there: a redirect that empties it (`>`, `>|`, `&>`),
a copy or a move onto it or into a folder that holds a file of its name (not `cp -n`), `ln -f`, `tee` without `-a`, `dd`
without `conv=notrunc`. The shell keeps no copy of what it replaces, unlike the file tools, whose changes `dotx undo`
writes back.

Who answers what the policy asks depends on the approval mode of the run: you, a review model or nobody (see
[Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)). The second step and your hand-off rules
hold in every mode, and so do the hand-offs of the fourth step and the questions of the fifth, `yolo` too (item 427):
what the first step refuses, a `yolo` run asks you. What a `yolo` run waives are the questions of the third step, of
your `ask` rules, of the preset and of untrusted content, and it records each.

**Untrusted content.** Web pages, screens, MCP results and the mail and calendars of the accounts you signed in (item
424) can be written by third parties. Dot wraps them in a fence with a random marker per call (`<web_content …>`,
`<screen_content …>`, `<account_content …>`) and tells the model that what is inside is data,
never instructions; only the end that carries the marker closes a fence, and a closing tag inside, in any case, is
broken. The reply of a teammate that read such content and the files you attach to a message are fenced the same way.
What a shell command printed after it reached the network (`curl`, `ssh`, `git clone`, `gh`, a line
the guard cannot read), what a terminal program prints, and a find_help answer that lists skills or MCP servers that
other agents of the Mac set up count the same way, unfenced. A run that has seen such content is marked untrusted, and
from then on outward-facing or lasting actions (messages, posts, follows and likes, privilege, system settings, saving a
schedule or a note, and a shell line that sends data out: an upload or a form, a push, a copy to another machine, a
change on a code host, inline code that reaches the network or runs a command of it) ask even when the preset or an
`allow` rule would let them through; only a `preapproved` rule skips the question. A message you send to the run clears
the mark. A delegated run starts as untrusted as its parent, a resumed run as the run it resumes was when it was cut off,
and the run that tells a chat that its command in the background ended as the chat's last run ended: that message is
Dot's, not yours. A message you send starts a run unmarked. A note a run saved while it was marked keeps that origin
(`tainted`): it is never among the notes a run starts with, is handed while it works or as a site's lessons (item
384), a `memory_search` that returns one marks the run that read it, so its next save asks again, and Settings › Memory
and `dotx memory list` say of such a note that it was saved while reading outside content.

**Secrets.**

- The memory tools refuse a note that holds a key-like string or one of the secrets Dot holds (`memory_save`,
  `memory_update`), and tell the model to record where the key lives, not the key. A note you write yourself, with
  `dotx memory add` or on the Memory page, is yours and not refused.
- The browser and desktop tools never type into password, one-time-code and card fields themselves: the field that
  would take the text is checked, whatever element the step names (a desktop `menu` step that pastes is not). Outside
  `yolo` they refuse; in `yolo` the field is handed to you (item 427), and on a browser step you can take the browser
  over to fill it. In the same way `terminal_send` types nothing, in any mode, while the program's last line asks for
  a password, a passphrase or a PIN: that step is yours, in a terminal of your own. The desktop tools never control a
  password manager or Keychain Access, in any mode, nor a window of Dot's outside `yolo`: the desktop app's, or one of
  another application (a browser, an installed web app) that shows the web UI, known at each step by the title the UI
  gives its page (its approvals, rules and settings decide what a run may do; a page of another site that takes such a
  title is refused as well). Desktop screenshots black out the password managers' windows; secure fields and web pages
  are not masked.
- Environment variables whose names look like secrets (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL` or
  `AUTH` in the name) are removed from the processes the shell tool starts, `SSH_AUTH_SOCK` among them, so commands do
  not reach the keys of your SSH agent (`[shell] pass_env` lists exceptions).
- Outside a `yolo` run (which asks you first, item 427) the agent can neither read nor change `~/.ssh`, `~/.gnupg`,
  `~/Library/Keychains`, `~/.aws`,
  `~/.config/gcloud`, the data of your browsers (Chrome, Safari, Firefox, Brave, Edge and Arc) and their cookies, the
  tokens and passwords that command lines keep (`~/.netrc`, `~/.git-credentials`, `~/.config/gh`, `~/.npmrc`,
  `~/.pypirc`, the credentials of Cargo and RubyGems, `~/.docker/config.json`, `~/.kube`, `~/.azure`, `~/.config/op`,
  `~/.password-store`), the shell's history, Messages and Mail (`[policy] protected_paths`; a list of your own replaces
  this one), nor Dot's own key file, token, what signing in to an MCP server gave it, browser profiles, configuration,
  database and journals of file changes. The
  configuration holds the policy and the MCP servers, the database the rules, the journals what `dotx undo` writes back.
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
  in your place; OpenCode's own agent, which writes past the journal `dotx undo` reads, is refused. The shell guard
  refuses a line that writes there: by a copy or a move (its target folder option too), `dd`, a redirect, a download, a
  clone, an unpacked archive, git run in one of those folders, a file edited in place (`sed -i`, `perl -i`, `ruby -i`)
  or inline code that names the place (in a word of switches such as `-pe` too). The guard reads the line, not what a
  program does; the sandbox below holds what it cannot read. A plugin's MCP servers, and those you added, start with the
  daemon and are given keys from the key store: put a plugin or a skill in place yourself.
- Outside a `yolo` run the processes of a run start in a macOS sandbox (`sandbox-exec`; `[shell] sandbox`, on by
  default): a shell command, a terminal session, an AppleScript and what it runs can neither read nor change Dot's own
  files above (its key file, token, push key, MCP sign-ins, browser profiles, configuration, database and journals,
  and the data
  directory of your Chrome), nor change what Dot loads and runs, nor rename a folder above any of them, whatever program
  does it: a script the run wrote, code that builds the path, a link, another letter case. macOS answers
  `Operation not permitted`. The search tools may not read your protected locations either; a command still may (ssh and
  git read their keys there), and for those the guard reads its line. A search that meets a place it may not read shows
  what it found elsewhere and names the places it did not read, as a search of any run does with a folder whose reading
  macOS broke off (it does now and then under `~/Library/Containers` when the home folder is searched). macOS starts no
  program that runs as root inside the sandbox (`ps`, `top`, `sudo`, `crontab`): `pgrep -lf` and `lsof` work, a `yolo`
  run runs them, `[shell] sandbox = false` turns the sandbox off. Nor does a process of a run have another program act
  for it, outside the sandbox (item 418): it opens nothing through Launch Services (`open`, a program's own call),
  starts neither the `launchctl`, `shortcuts` nor `lsregister` that macOS installs and reaches neither the Shortcuts
  service nor the Launch Services database they would hand the work to, whatever program asks, creates no job of
  launchd, and writes nothing into `~/Library/LaunchAgents`, where launchd finds the jobs it starts at a login. These
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
The click questions are asked in every mode, `yolo` too, where a payment, a message or a post they find is yours as well
(item 427). Two more checks run before a model call when the judge is on. A `browser_act` that names what it expects to
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
counts as a refusal. The note you write with your answer reaches the model. This is the default approval mode, `ask`,
and `supervised` asks before every change as well; in `auto` and `yolo` the questions of the policy are answered for
you, but for what is yours in every mode (item 427): money, credentials, messages and posts in your name, and in `yolo`
a read of your secrets or data sent out, each a card in the chat. A question of the agent itself (`ask_user`) waits the
same way, except in a run nobody can answer during it (see
[Autonomy](autonomy.md#autonomy-approvals-goal-mode-and-the-end-of-a-run)). Its options are rows on the card, each with
what it leads to, and the one Dot recommends is marked: a row sends its option, or, when several may be chosen, the rows
ticked are sent with what you write, as one answer. In the terminal you type an option's number (several, separated by
commas) or your own words. The run reads which option you chose, and whether it was the one it recommended.

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
> asks about (item 385), and outside yolo the sandbox refuses the opening itself (item 418). It does not follow what a
> recursive command does inside the folders it walks, nor the code of a script it runs; the sandbox keeps those out of
> Dot's own files, not out of yours, and a shell can do more than any analysis can foresee. Treat Dot as a user of your
> account with the rights of your account: keep backups, keep the workspace for scratch work, and use `balanced` or
> `cautious` on a machine where a mistake would be expensive.
