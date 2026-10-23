# Security policy

## Reporting a vulnerability

Report a security problem privately, through GitHub's private vulnerability reporting:
[Report a vulnerability](https://github.com/senoldogann/Dot/security/advisories/new) on the Security tab of this
repository. Please do not open a public issue, discussion or pull request for it.

Say what you found and what it lets someone do, the steps or the input that show it, the commit you tested and your
macOS version. The maintainer reads every report; there is no fixed time for an answer. A fix lands on `main` before
the details are made public.

## What is in scope

Dot runs with your rights on your Mac, so the boundaries it keeps matter most:

- the daemon's API and the web UI: the token, the checks of where a request comes from, what a page can reach;
- what a run may do without asking: the approval modes, the shell guard, the policy for content that third parties can
  author, the sandbox of a run's processes;
- secrets: what reaches a model, the logs, the events or the API, and the masking of what does;
- the browser and desktop tools, and the MCP servers, skills and plugins that Dot loads.

A prompt injection that the approval modes and the policy stop as they are meant to is not a vulnerability; one that
gets past them is. The shell guard is best-effort ([Status and known limits](docs/limits.md)), and a way past it is
worth a report.

## Supported versions

Dot is developed on `main`, and fixes land there. Only the latest release is supported: a fix ships as a new version,
which the desktop app's updater offers, and older versions get no backports.
