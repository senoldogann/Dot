## Summary

The four points of the owner's 2026-10-28 list, in one item. The second — the form payloads — is the one the owner
asked for "especially"; the other three were partly there and are now the missing piece.

`security_scan` (new, in the `recon` group) reads a page's forms and tries a small, read-only set of payloads on them:
SQL injection, reflected XSS, a login bypass and default credentials. `depth = "forms"` lists the forms without
sending anything. The host must be in `[recon] owned_hosts`, every form action must stay on that host, hidden fields
(a CSRF token) go back as the page gave them, attempts are capped, and the report names the sign, not the page.

```
security_scan(target, depth)
 ├─ checked_address        → scheme, browser allow/block list, exfiltration flags
 ├─ owned(host, owned_hosts) → a host outside [recon] owned_hosts is refused
 ├─ web.get(page)          → decode_body → forms_on (action, method, hidden/select/textarea)
 ├─ _attempts              → SQLi/XSS per text field; login bypass + default creds on a password form
 └─ web.post_form / get    → _judge → SQL error, exact payload reflected, a signed-in sign
```

`WebClient.post_form` sends a form's fields as `application/x-www-form-urlencoded` and follows no redirect, so a
login's status and `Location` are what is read. It goes through the same checked transport as `get`.

The other three:

- `browser_eval.selector`: the script gets the first element a CSS selector matches, so
  `browser_eval(script="(el) => el.click()", selector="button[aria-label='Send']")` is the Fast-Track click a
  snapshot, a find and a step would take three calls to reach.
- `browser_act.retry`: a click whose batch left the page unchanged is tried once the other way before the result
  returns — the same ref gets the pointer sequence a script sends, then the page is read again. A click that moved the
  page is never sent twice; a failed step is left alone.
- The built-in skill `deep-os` and `docs/deep-os.md`: the command behind a Mac task (Wi-Fi by `networksetup`, sound
  and appearance by `osascript`, power by `pmset`, ports by `lsof`, processes by `pgrep`), with the destructive
  commands that wait for the owner's word.

## Evidence

- **Before:** no security tool; `recon` sent no payload (item 776's roadmap said an exploit chain is not a tool a run
  needs). `browser_eval` had refs but no selector; `browser_act` said a click left the page unchanged and named
  `browser_eval`, but the model had to take that way. No deep-OS map.
- **After:** `tests/unit/test_security.py` 8 passed (refused host, forms without a payload, the four findings, the
  cross-host form, the cap, the parser); `tests/unit/test_browser_retry.py` 2 passed; `tests/smoke/test_browser_eval.py`
  17 passed with Chrome, including a page that ignores a real click and answers the retry's synthetic one;
  `tests/unit` 6197 passed, the five failures being this Mac's plugin gallery, the service-install tests and a flaky
  rule timeout (all pre-existing on `main`); smoke 1112 passed, 1 skipped; `ruff` and `mypy` clean.
- The context budget is 21,984 of 22,100; the `recon` group's line names `security_scan`. The scaffold's small window
  moved from 35,200 to 37,000 for the fixed part it grew.

## Merge Danger

- **Door:** a new tool in the existing `recon` group; nothing runs unless a run calls it. It refuses a host outside
  `[recon] owned_hosts`, skips a form action on another host, sends read-only payloads, and has no exploit, WAF
  evasion, session or C2. The line item 776 drew is kept: a form's question is a tool a run needs; an attack chain is
  not.
- **Blast radius:** `web/client.py` gains `post_form` (same checks, no redirect follow). `browser_act`'s retry runs
  one script only on a click whose page did not change; `browser_eval` gains an optional field. The prompt grows 600
  tokens, paid for in the budget test.
- **After merge:** the daemon must restart to load `security_scan` and the `deep-os` skill; no config change is needed
  (`[recon] owned_hosts` already gates it).
