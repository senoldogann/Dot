"""Whether main's newest commit has passed its macOS checks, and the one issue that says when it has not (item 1899).

A pull request runs the Linux checks alone (item 1886); the macOS suites (the gate's macOS unit and smoke tests, the
browser parts, the iPhone app's short set) run on the push to main that merges it. So the owner's Dot is updated only
from a commit whose macOS runs passed, and a red one is seen:

- ``check``: prints the verdict for main's newest commit (or ``--sha``) and exits 0 when it is green, 1 when a run of it
  failed and 2 while one is still to come. Run it before updating the owner's Dot (docs/development.md).
- ``report``: the last job of each macOS workflow on main runs it (``--this-run``: its own run, not finished while it
  runs, is read from its other jobs). A red verdict opens the issue "main is red on macOS", or adds a comment to the
  open one, with the runs and jobs that failed; a green one closes it; a pending one leaves it.

The runs read are the newest run of ``gate.yml`` on main for the commit (the push's own or a run by hand's), the
newest run of ``ios.yml`` for a push to main, whatever its commit: ios.yml runs only for a push that changed ``ios/``,
so its last verdict stands until a later push changes the app again (a commit that did not touch ``ios/`` does not make
a red iPhone run green), and the newest finished run of ``browser.yml`` on main (item 1936): the whole browser suite
runs each night, or by hand (``gh workflow run browser.yml --ref main``), and a push runs only the parts its files can
affect, so a red night stands until a later run of the whole suite passes. A browser run still going is not waited
for (it takes hours, every night), and one cancelled (a newer run replaced it) says nothing. A commit with no gate run
yet is pending: runs of main that wait replace one another (item 1011), so the newest commit's run is the one that
comes.
Stdlib only, as the job that runs it installs nothing; GitHub is asked through ``gh``.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

ISSUE_TITLE: str = "main is red on macOS"
GATE: str = "gate.yml"
IOS: str = "ios.yml"
BROWSER: str = "browser.yml"
WORKFLOWS: tuple[str, ...] = (GATE, IOS, BROWSER)
GREEN: frozenset[str] = frozenset({"success", "skipped"})
# The job of each macOS workflow that runs ``report`` (gate.yml, ios.yml): its own run is read from its other jobs.
REPORT_JOB: str = "main-health"

Verdict = Literal["green", "red", "pending"]


@dataclass(frozen=True)
class Run:
    """The newest run of one workflow for the commit: its state, its page, and the jobs that failed in it."""

    workflow: str
    status: str
    conclusion: str
    url: str
    failed_jobs: tuple[str, ...]


def verdict(runs: list[Run]) -> Verdict:
    """Red when a finished run did not pass; pending while the gate has no run or one is not finished; else green."""
    if any(run.status == "completed" and run.conclusion not in GREEN for run in runs):
        return "red"
    if not any(run.workflow == GATE for run in runs) or any(run.status != "completed" for run in runs):
        return "pending"
    return "green"


def report_body(sha: str, runs: list[Run]) -> str:
    """The issue's words for a red commit: each run that failed and its failed jobs."""
    lines: list[str] = [f"main's commit {sha} failed on macOS:", ""]
    for run in runs:
        if run.status == "completed" and run.conclusion not in GREEN:
            jobs: str = ", ".join(run.failed_jobs) or "no job named"
            lines.append(f"- {run.workflow}: {run.conclusion} ({jobs}) {run.url}")
    lines += ["", "Fix main before anything else is merged; the owner's Dot is not updated until it is green."]
    return "\n".join(lines)


def _gh(arguments: list[str]) -> str:
    """``gh`` with the arguments; its error, with what was asked, when it fails."""
    done: subprocess.CompletedProcess[str] = subprocess.run(
        ["gh", *arguments], check=False, capture_output=True, text=True
    )
    if done.returncode != 0:
        raise RuntimeError(f"gh {' '.join(arguments)} failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def _main_sha() -> str:
    return _gh(["api", "repos/{owner}/{repo}/commits/main", "--jq", ".sha"]).strip()


def _jobs(run_id: str) -> list[dict[str, object]]:
    listed: object = json.loads(_gh(["api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs?per_page=100"]))
    if not isinstance(listed, dict) or not isinstance(listed.get("jobs"), list):
        raise RuntimeError(f"the jobs of run {run_id} came back as {listed!r}")
    return [job for job in listed["jobs"] if isinstance(job, dict)]


def _failed(jobs: list[dict[str, object]]) -> tuple[str, ...]:
    return tuple(str(job.get("name")) for job in jobs if job.get("conclusion") not in (*GREEN, None))


def this_run(workflow: str, url: str, jobs: list[dict[str, object]]) -> Run:
    """The run the report job is part of, from its other jobs: it is not finished while the report runs."""
    others: list[dict[str, object]] = [job for job in jobs if job.get("name") != REPORT_JOB]
    failed: tuple[str, ...] = _failed(others)
    finished: bool = all(job.get("status") == "completed" for job in others)
    return Run(
        workflow=workflow,
        status="completed" if finished or failed else "in_progress",
        conclusion="failure" if failed else "success" if finished else "",
        url=url,
        failed_jobs=failed,
    )


def query(workflow: str, sha: str) -> str:
    """Which runs of ``workflow`` on main are asked for: the gate's of this commit; the iPhone app's last of a push,
    whatever its commit; the browser suite's last few, whatever their event, as :func:`newest` skips some."""
    if workflow == GATE:
        return f"head_sha={sha}"
    if workflow == IOS:
        return "event=push&per_page=1"
    return "per_page=20"


def newest(workflow: str, runs: list[dict[str, object]], current: str | None) -> dict[str, object] | None:
    """The run of ``workflow`` that speaks for main, of those GitHub listed (``None`` when none does). The newest for
    the gate and ios.yml; for the browser suite the newest finished and not cancelled, or the run ``current`` (the
    report job's own) when it is listed, as a night's own report reads its own run."""
    if workflow == BROWSER:
        runs = [
            run
            for run in runs
            if (current is not None and str(run.get("id")) == current)
            or (run.get("status") == "completed" and run.get("conclusion") != "cancelled")
        ]
    return max(runs, key=lambda r: str(r.get("created_at", ""))) if runs else None


def _runs(sha: str, current: str | None) -> list[Run]:
    found: list[Run] = []
    for workflow in WORKFLOWS:
        listed: object = json.loads(
            _gh(
                [
                    "api",
                    f"repos/{{owner}}/{{repo}}/actions/workflows/{workflow}/runs?branch=main&{query(workflow, sha)}",
                ]
            )
        )
        if not isinstance(listed, dict) or not isinstance(listed.get("workflow_runs"), list):
            raise RuntimeError(f"the runs of {workflow} for {sha} came back as {listed!r}")
        runs: list[dict[str, object]] = [r for r in listed["workflow_runs"] if isinstance(r, dict)]
        chosen: dict[str, object] | None = newest(workflow, runs, current)
        if chosen is None:
            continue
        url: str = str(chosen.get("html_url", ""))
        if current is not None and str(chosen.get("id")) == current:
            found.append(this_run(workflow, url, _jobs(current)))
            continue
        status: str = str(chosen.get("status", ""))
        conclusion: str = str(chosen.get("conclusion") or "")
        failed: tuple[str, ...] = (
            _failed(_jobs(str(chosen["id"]))) if status == "completed" and conclusion not in GREEN else ()
        )
        found.append(Run(workflow=workflow, status=status, conclusion=conclusion, url=url, failed_jobs=failed))
    return found


def _open_issue() -> int | None:
    listed: object = json.loads(
        _gh(["issue", "list", "--state", "open", "--search", f'in:title "{ISSUE_TITLE}"', "--json", "number,title"])
    )
    if not isinstance(listed, list):
        raise RuntimeError(f"the open issues came back as {listed!r}")
    numbers: list[int] = [
        int(issue["number"]) for issue in listed if isinstance(issue, dict) and issue.get("title") == ISSUE_TITLE
    ]
    return min(numbers) if numbers else None


def _report(sha: str, said: Verdict, runs: list[Run]) -> None:
    issue: int | None = _open_issue()
    if said == "red":
        body: str = report_body(sha, runs)
        if issue is None:
            _gh(["issue", "create", "--title", ISSUE_TITLE, "--body", body])
        else:
            _gh(["issue", "comment", str(issue), "--body", body])
    elif said == "green" and issue is not None:
        _gh(["issue", "close", str(issue), "--comment", f"main's commit {sha} passed on macOS."])


def main(arguments: list[str]) -> int:
    if not arguments or arguments[0] not in ("check", "report"):
        raise SystemExit("usage: main_macos_green.py check [--sha SHA] | report --sha SHA --this-run RUN_ID")
    sha: str = arguments[arguments.index("--sha") + 1] if "--sha" in arguments else _main_sha()
    current: str | None = arguments[arguments.index("--this-run") + 1] if "--this-run" in arguments else None
    runs: list[Run] = _runs(sha, current)
    said: Verdict = verdict(runs)
    for run in runs:
        print(f"{run.workflow}: {run.status} {run.conclusion} {run.url}")
    print(f"main {sha} on macOS: {said}")
    if arguments[0] == "report":
        _report(sha, said, runs)
        return 0
    return {"green": 0, "red": 1, "pending": 2}[said]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
