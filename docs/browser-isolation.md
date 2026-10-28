# Browser isolation: what is separate, and what is shared on purpose

The owner asked for the most performant, least confusing arrangement, not a per-run browser. This is the decision and
the evidence behind it (item 805, 2026-10-28).

## What is separate today

- **Tabs.** `ThreadTabs` keeps a set of tabs for each conversation thread. A run acts only on its thread's tabs; it
  cannot click, read or close another thread's tab, and never touches the tabs the owner opened.
- **Eval attempts.** Every eval attempt runs with a throw-away `DOT_HOME` and a `DOT_CHROME_DIR` under it
  (`eval_environment` in `src/dotagent/evals/runner.py`). It starts with no cookies, no sign-ins and no database; the
  owner's Chrome profile is never opened.
- **The owner's own Chrome.** Dot's own Chrome (`mode = "own"`) uses a profile under Dot's home, not the owner's
  default Chrome profile; in attach mode Dot works only in tabs it opened.

## What is shared on purpose

Normal task and research runs share one Chrome profile, so a site the owner signed in to once is signed in for the next
run. That is not an accident: a per-run context would make the owner sign in again in every run, and a per-agent
profile would need a Chrome restart whenever the agent changes. Both cost more than they protect for a single-user Mac
where every run is the owner's.

The remaining risk is real and named: a malicious page or a prompt injection in one run can use the cookies of the same
profile in another run, and a logged-in session is a capability. The system's answer is `Trust and safety` on the page
content, approvals before sensitive actions, and the `vault` for logins; it is not a second browser.

## Decision

Keep the shared profile for normal and research runs. Keep eval attempts in their own throw-away home and Chrome
directory. Do not build per-run contexts or a profile per agent now; the first is slower and loses sign-ins, the second
needs a restart and a profile picker the owner did not ask for.

If isolation is needed later, start with a **profile per agent** (`[browser] profile`) rather than a context per run:
it keeps sign-ins working, gives the strongest boundary Chrome offers, and only changes one page at the moment an agent
is picked. The cost is a Chrome restart when two agents with different profiles run at the same time, which is why it
is not the default today.

## The test that keeps the eval claim true

`tests/unit/test_browser_isolation.py` checks that an eval attempt receives a `DOT_HOME` and a `DOT_CHROME_DIR` under
that home, and that the process environment is put back when the attempt ends. If that ever stops being true, the
"eval cannot see the owner's cookies" sentence above is false.
