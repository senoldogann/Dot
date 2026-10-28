# Security scan: the forms of a host you own

`security_scan` (item 781) reads the forms of a page on a host in `[recon] owned_hosts` and tries a small, benign set
of payloads on them: SQL injection, reflected XSS, a login bypass and default credentials. It is the quick check an
owner runs after shipping a form, before a real assessment.

It is not a scanner for someone else's site. The host must be in `[recon] owned_hosts` (a name there covers its
subdomains), every form action must stay on that host, the payloads are read-only, the requests are capped, and the
report says what a form answered instead of dumping a page.

## Use

```json
{"target": "https://example.com/login", "depth": "forms"}
```

lists the forms and their fields without sending anything. Then:

```json
{"target": "https://example.com/login", "depth": "payloads", "max_attempts": 40}
```

tries the payloads. `max_attempts` (1–120, default 40) counts form submissions, not fields; each waits for its answer
and the next one follows after a short pause. The tool runs in the `recon` tool group and is available in every run
mode; it is a high-risk network action, so a mode that asks before such an action asks here too.

## What it tries

| Class | Payloads | What the answer shows |
| --- | --- | --- |
| SQL injection | a quote, a tautology (`' OR '1'='1`), a false one, `admin'--` | a database error's words in the page |
| Reflected XSS | `<script>alert(1)</script>`, an `svg onload`, an `img onerror` | the exact payload comes back unescaped |
| Login bypass | `' OR '1'='1' -- `, `admin'--`, `' OR 1=1#`, `" OR ""="` | a redirect away from the login, a cookie and no refusal, or a signed-in page |
| Default credentials | admin/admin, admin/password, root/root, test/test, and three more | the same signed-in signs |

A login form is found by its password field; every other form is tried field by field. Hidden fields keep the value
the page gave them (a CSRF token is sent back), a checkbox and a radio keep the page's value, and a select keeps its
selected option. A form that posts to another host is skipped and said to be.

## What it does not do

- It does not test a host outside `[recon] owned_hosts`; that is the one gate, and it is the same one `recon`'s port
  scan uses.
- It writes nothing, drops nothing and takes nothing: the SQL payloads ask a question, the XSS payload only shows
  whether the page encodes its input, and the default credentials are the first ones a fresh install leaves behind,
  not a credential list.
- It does not try to get around a WAF, a CAPTCHA or a rate limit, and it does not keep a session: a login is judged by
  its answer, not by following it into the site.
- It is a quick check, not a substitute for a full assessment. A clean result says these forms answered these
  payloads without a sign; it does not say the site is secure.

## The report

The result opens with the host, the page, the forms and how many attempts were sent, then a finding for each sign:

```
Security scan of example.com: 2 form(s) on /login, 18 attempt(s).

  /login (POST): username, password
  /search (GET): q

Findings
  [high] Login bypass — /login (username): `' OR '1'='1' -- ` answered 302 to /dashboard
  [medium] Reflected XSS — /search (q): `<script>alert(1)</script>` came back unescaped
```

The page's own words in a finding are untrusted input, and the result is marked as such. Fix the form: a parameterized
query, output encoding, a check that does not trust the name, and no default account.

## A note on permission

The scan is for a host the owner listed as theirs. Running it against a host you do not own is not a test; it is an
attack on someone else's system, and the law treats it that way. If a client asks for one, their written permission
and the scope belong in the run before the target does.
