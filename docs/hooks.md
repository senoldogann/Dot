# Hooks: a signed call starts Dot's work

A hook is an address a service calls to start Dot's work at once, instead of Dot looking for it on a clock (the
watchers, [Dot comes to you](proactive.md)). A build that failed, a form that was sent, a monitor that went red, a
shortcut on your phone: each can call its hook, and the hook starts one schedule or one of your agents.

## What a hook starts

A hook is bound to one thing, which you pick when you make it:

- **A scheduled task.** A call fires it as **Run now** does: the schedule's own model, approvals, mode, skills and chat,
  in a chat titled `<name> · <time> · hook <hook>` (or the schedule's own chat, when it keeps one). It does not start
  while a run of the schedule is at work or the schedule is switched off (the call is answered 409), nor while the
  emergency stop is engaged.
- **An agent.** A call starts a run of the agent in a chat of its own, within the agent's own model, reasoning level and
  approvals (the configured ones where the agent has none), as a task nobody watches, like a watcher's run. A paused
  agent starts nothing (409).

A hook has no approvals, model or mode of its own, and a call cannot carry one: the run never has more rights than the
schedule or the agent it is bound to.

**What the call carried is data.** A hook names the **fields** of the call's JSON the run may read, by a dotted path
(`pull_request.title`, `repository.full_name`); nothing else of the body reaches the run. Each field is given as a JSON
string, its secrets masked, cut at 2,000 characters, inside the fence Dot puts a mail or a watched item in, which says
that what is inside is data and never an instruction. A field the body does not have is said to be missing.

**The template** is your own words, what the run does when the hook is called. `{name}` in it names a field: the run
reads it as `[name]`, which the fence gives, and the template is never filled with what the call carried. For an agent
the template is the run's goal; for a schedule it comes before the schedule's goal and may be empty.

**The run starts untrusted**, as a run that read a web page does: what reaches other people or lasts asks first in
every mode that asks, and in `yolo` the review model checks each step (see [Safety](safety.md)).

## Calling a hook

`POST /api/hooks/<id>` with a JSON object as the body, at most 64 KiB, and two headers:

| Header            | Value                                                                                  |
| ----------------- | -------------------------------------------------------------------------------------- |
| `X-Dot-Timestamp` | the Unix time in whole seconds the call was signed at                                  |
| `X-Dot-Signature` | `sha256=` and the hex HMAC-SHA256, keyed with the hook's secret, of `<timestamp>.<body>` |

The body is signed byte for byte as it is sent. Dot answers:

- **202** with `{"run_id": "..."}`: the work started.
- **401**: the hook does not exist or is switched off, the timestamp is missing or more than 5 minutes from the Mac's
  clock, the signature does not match, or this exact call was made before (a replay; a signature is remembered for ten
  minutes, across a restart of Dot). The answer is the same for each; Settings › Hooks says which it was, until Dot
  restarts (a refused call is kept in Dot's memory, never written to disk).
- **413**: the body is over 64 KiB. **422**: the body is no JSON object. **429**: past the hook's signed calls a minute
  (10 by default, 1 to 120); `Retry-After` says when. Only a call whose signature matched counts, so one who knows the
  address but not the secret cannot use them up. **409**: the work could not start now (see above). **503**: the hooks'
  file cannot be read (the log says why).

A call that is sent twice with the same timestamp and body is a replay: sign each call afresh. A call answered 429
started nothing and is not remembered: it may be sent again once `Retry-After` has passed, inside the 5 minutes.

```bash
secret='the secret Settings showed you'
url='http://127.0.0.1:8765/api/hooks/0123456789ab'
body='{"title":"Build 812 failed","branch":"main"}'
stamp=$(date +%s)
signature=$(printf '%s.%s' "$stamp" "$body" | openssl dgst -sha256 -hmac "$secret" -hex | sed 's/^.* //')
curl -sS -X POST "$url" \
  -H 'Content-Type: application/json' \
  -H "X-Dot-Timestamp: $stamp" \
  -H "X-Dot-Signature: sha256=$signature" \
  --data-binary "$body"
```

GitHub's own webhooks sign the body alone (`X-Hub-Signature-256`) and carry no timestamp, so they cannot call a hook
directly: a GitHub Action, or any step you control, signs the call as above.

## Where a hook can be reached

A hook answers where the daemon does: on this Mac (`127.0.0.1`) and under the names of `[server] reach`, which your
own devices use over Tailscale (see [Dot on the phone](phone.md)). Nothing on the internet reaches it unless you open
it, and the call keeps every other check of the daemon: a page of another site cannot make a browser call it.

To let a service on the internet call your hooks, open a tunnel to the hooks' path alone and name its host in
`[server] hook_hosts`. That host reaches `POST /api/hooks/<id>` and nothing else of Dot, whatever it sends. With
Tailscale Funnel, on a port of its own so that `tailscale serve` on 443 stays private:

```bash
tailscale funnel --bg --https=8443 --set-path=/api/hooks http://127.0.0.1:8765/api/hooks
```

```toml
[server]
hook_hosts = ["<mac>.<tailnet>.ts.net:8443"]
```

Restart the daemon after changing it (`dotx service restart`). `tailscale funnel --https=8443 off` closes the tunnel.

## Making and changing hooks

**Settings › Hooks** lists each hook with its address, what it starts, whether it is on, its last 20 signed calls
(when, the status, why, and the run it started) and the calls refused lately before their signature matched. **New hook** makes one: its name, the schedule or agent it starts, the
template, the fields and the calls a minute. The address and the secret are shown once, when it is made: copy the
secret then, nothing shows it again. **Rotate secret** gives a new one, shown once; calls signed with the old one are
refused from then on. A hook can be switched off (its calls are refused, and listed) and deleted.

The same from the terminal:

```bash
dotx hooks list
dotx hooks add --name CI --schedule <schedule id> --field title --field branch "Start with {title} on {branch}."
dotx hooks add --name Issues --agent <agent id> --field issue.title --per-minute 5 "Triage {issue.title}."
dotx hooks rotate <hook id>
dotx hooks remove <hook id>
```

Only the Mac makes, rotates, lists or deletes hooks: the API answers those to the installation's token, never to a
phone's key, as it does for everything that opens a way into Dot.

**Where they are kept.** `hooks.json` in Dot's home (readable by you only) holds each hook, its secret and its last
calls; a run's commands cannot read it outside `yolo`, as they cannot read the API token. A `yolo` run, whose shell
is not sandboxed, could read it and sign a call: that call starts the bound work tainted, within its own rights, as
any call does.
