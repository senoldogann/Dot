"""The release publishes only what the gate passed again (item 457), the core of the browser tests with it (item
486): the publish job needs the tag's job and the gate's, keeps the environment the PyPI publisher names, and is the
only job with an identity token; the gate can be called, and its browser job runs there as on every change. Every
browser test runs at night and on demand. Read from the workflow files themselves, so an edit that drops one is caught
before a tag is pushed."""

import re
from pathlib import Path

import yaml

from dotagent.core.types import JsonObject

_WORKFLOWS: Path = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _workflow(name: str) -> JsonObject:
    loaded: object = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), name
    return loaded


def test_nothing_is_published_unless_the_tag_the_gate_and_the_browser_tests_passed() -> None:
    release: JsonObject = _workflow("release.yml")
    # The workflow's own permissions read the code and nothing else, so no job takes an identity token from them.
    assert release["permissions"] == {"contents": "read"}
    jobs = release["jobs"]
    assert isinstance(jobs, dict)
    # A job's permissions are a list of what it may do, never all of it ("write-all").
    assert all(isinstance(job.get("permissions", {}), dict) for job in jobs.values() if isinstance(job, dict))
    publish = jobs["publish"]
    assert isinstance(publish, dict)
    assert set(publish["needs"]) == {"tag", "gate"}
    assert publish["environment"] == "pypi"
    assert jobs["gate"] == {"needs": "tag", "uses": "./.github/workflows/gate.yml"}
    # The gate the release calls runs the core of the browser tests, in its parts. A release is a workflow_call, not a
    # pull request, so the condition that skips a part for a pull request is true there and the parts run.
    gate_jobs = _workflow("gate.yml")["jobs"]
    assert isinstance(gate_jobs, dict)
    browser_if = str(gate_jobs["browser"].get("if", ""))
    assert "github.event_name != 'pull_request'" in browser_if
    assert "needs.changes.outputs.browser == 'true'" in browser_if
    assert [part["name"] for part in gate_jobs["browser"]["strategy"]["matrix"]["part"]] == [
        "pages",
        "phone 1",
        "phone 2",
    ]
    # The checks that do not import PyObjC run on Linux; the tests stay on macOS. `ci` is what a required check can
    # name, because a skipped browser part reports no check of its own.
    assert gate_jobs["checks"]["runs-on"] == "ubuntu-latest"
    assert gate_jobs["gate"]["runs-on"] == "macos-latest"
    assert gate_jobs["ci"]["runs-on"] == "ubuntu-latest"
    assert gate_jobs["ci"]["if"] == "always()"
    assert set(gate_jobs["ci"]["needs"]) == {"prose", "checks", "gate", "smoke", "browser"}
    assert not any("ruff " in str(step.get("run", "")) for step in gate_jobs["gate"]["steps"])
    assert any("ruff format" in str(step.get("run", "")) for step in gate_jobs["checks"]["steps"])
    assert any("npm run check" in str(step.get("run", "")) for step in gate_jobs["checks"]["steps"])
    # And both halves of the smoke tests (item 944), each on its own runner, with nothing that skips them there but a
    # pull request that changes prose alone: the `prose` job tells the heavy jobs to skip, and a required check that a
    # path filter skips would never be reported at all.
    prose_skip = (
        "${{ !cancelled() && (github.event_name != 'pull_request' || needs.prose.outputs.prose_only != 'true') }}"
    )
    assert gate_jobs["smoke"]["if"] == prose_skip
    assert gate_jobs["checks"]["if"] == prose_skip
    assert gate_jobs["gate"]["if"] == prose_skip
    assert gate_jobs["prose"]["if"] == "github.event_name == 'pull_request'"
    assert gate_jobs["prose"]["outputs"]["prose_only"] == "${{ steps.decide.outputs.prose_only }}"
    assert not _workflow("gate.yml")[True]["pull_request"]
    halves = [
        step
        for job in (gate_jobs["gate"], gate_jobs["smoke"])
        for step in job["steps"]
        if "--part " in str(step.get("run", ""))
    ]
    assert sorted(re.findall(r"--part (\S+)", str(step["run"]))[0] for step in halves) == ["1/2", "2/2"]
    assert all("if" not in step and "continue-on-error" not in step for step in halves)
    tokened: list[str] = [
        name
        for name, job in jobs.items()
        if isinstance(job, dict) and isinstance(job.get("permissions"), dict) and "id-token" in job["permissions"]
    ]
    assert tokened == ["publish"]
    steps = jobs["tag"]["steps"]
    assert any("merge-base --is-ancestor" in str(step.get("run", "")) for step in steps)
    # The remote-tracking branch by its full name, so that a tag called origin/main cannot stand for it.
    assert any("refs/remotes/origin/main" in str(step.get("run", "")) for step in steps)


def test_the_gate_can_be_called_and_runs_as_before_on_changes_and_on_main() -> None:
    # PyYAML reads the key ``on`` as True.
    triggers = _workflow("gate.yml")[True]
    assert isinstance(triggers, dict)
    assert "workflow_call" in triggers and "pull_request" in triggers and "push" in triggers
    browser = _workflow("browser.yml")[True]
    assert isinstance(browser, dict)
    assert set(browser) == {"workflow_dispatch", "schedule"}
