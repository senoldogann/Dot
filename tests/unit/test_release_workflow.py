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
    # The gate the release calls runs the core of the browser tests, in its parts. The release starts from the push of
    # its tag, and the called gate sees that push as its own event: only main's ref is read by the `changes` job and
    # skips the browser parts (item 1886), so a release's decision is not made and every part runs.
    gate_jobs = _workflow("gate.yml")["jobs"]
    assert isinstance(gate_jobs, dict)
    browser_if = str(gate_jobs["browser"].get("if", ""))
    assert "needs.changes.result != 'success'" in browser_if
    assert "needs.changes.outputs.browser == 'true'" in browser_if
    assert "!(github.event_name == 'push' && github.ref == 'refs/heads/main')" in browser_if
    changes_if = str(gate_jobs["changes"].get("if", ""))
    assert (
        "(github.event_name == 'pull_request' || github.event_name == 'push' && github.ref == 'refs/heads/main')"
        in changes_if
    )
    release_triggers = _workflow("release.yml")[True]
    assert isinstance(release_triggers, dict)
    assert release_triggers["push"] == {"tags": ["v*"]}
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
    assert set(gate_jobs["ci"]["needs"]) == {"prose", "checks", "gate", "smoke", "browser", "desktop", "zizmor"}
    assert not any("ruff " in str(step.get("run", "")) for step in gate_jobs["gate"]["steps"])
    assert any("ruff format" in str(step.get("run", "")) for step in gate_jobs["checks"]["steps"])
    assert any("npm run check" in str(step.get("run", "")) for step in gate_jobs["checks"]["steps"])
    # And both halves of the smoke tests (item 944), each on its own runner, with nothing that skips them there but a
    # pull request that changes prose alone (the `prose` job tells the heavy jobs to skip, and a required check that a
    # path filter skips would never be reported at all) or one whose files nothing of them reads (`changes`, its
    # python answer, scripts/python_ci.py; a push to main asks it too, item 1886); a decision that failed or was not
    # made runs them, so a release runs them.
    prose_skip = (
        "${{ !cancelled() && (github.event_name != 'pull_request' || needs.prose.outputs.prose_only != 'true') }}"
    )
    python_skip = (
        "${{ !cancelled() && needs.prose.outputs.prose_only != 'true' && "
        "(needs.changes.result != 'success' || needs.changes.outputs.python == 'true') }}"
    )
    assert gate_jobs["smoke"]["if"] == python_skip
    assert gate_jobs["checks"]["if"] == prose_skip
    assert gate_jobs["gate"]["if"] == python_skip
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


def test_ci_waits_for_the_desktop_checks_and_zizmor_of_a_pull_request() -> None:
    # Item 1189: a pull request runs them from the gate, never from a path filter of their own, so that `ci`, which
    # main's protection requires and Dependabot's merges wait for (dependabot-merge.yml), is red when one of them is.
    gate_jobs = _workflow("gate.yml")["jobs"]
    assert isinstance(gate_jobs, dict)
    for job, output in (("desktop", "desktop"), ("zizmor", "workflows")):
        assert gate_jobs[job]["uses"] == f"$/.github/workflows/{job}.yml"
        condition = str(gate_jobs[job]["if"])
        assert "github.event_name == 'pull_request'" in condition
        assert "needs.changes.result != 'success'" in condition
        assert f"needs.changes.outputs.{output} == 'true'" in condition
        triggers = _workflow(f"{job}.yml")[True]
        assert isinstance(triggers, dict)
        assert "workflow_call" in triggers and "pull_request" not in triggers


def test_only_dependabots_patch_and_minor_pull_requests_merge_by_themselves() -> None:
    # Item 1190: a major version waits for a person; one merge at a time, after main's gate passed on main's head as it
    # is, and main's gate is started after it, which a merge made with the workflow's token would not start.
    merge = _workflow("dependabot-merge.yml")
    assert merge["permissions"] == {}
    jobs = merge["jobs"]
    label = jobs["label"]
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in str(label["if"])
    # An earlier head's mark goes first, read as it is now, so a head whose metadata cannot be read stays unmarked; one
    # on hold is not marked, and the merge passes it over.
    assert "--remove-label dependabot-merge" in str(label["steps"][0]["run"])
    assert "gh pr view" in str(label["steps"][0]["run"])
    marking = [step for step in label["steps"] if "--add-label dependabot-merge" in str(step.get("run", ""))]
    assert len(marking) == 1
    assert set(re.findall(r"version-update:semver-\w+", str(marking[0]["if"]))) == {
        "version-update:semver-patch",
        "version-update:semver-minor",
    }
    job = jobs["merge"]
    assert job["concurrency"] == {"group": "dependabot-merge", "cancel-in-progress": False}
    script = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert '.author.login == "app/dependabot"' in script and 'select(.name == "ci")' in script
    assert 'index("dependabot-hold") | not' in script and "steps.labels.outputs.hold == 'false'" in str(
        marking[0]["if"]
    )
    assert script.count("gh pr merge") == 1 and "--match-head-commit" in script and "--auto" not in script
    # Main's gate on main's head must have passed; one that waits or runs is let finish first.
    assert script.index('select(.name == "ci")') < script.index('gh api "repos/$GH_REPO/commits/main"')
    assert script.index('any(.[]; .status != "completed")') < script.index("gh pr merge")
    assert ".headSha != $head" in script
    # Only the locks and manifests merge alone: pyproject.toml's dependencies are under "Always needs a human".
    allowed = "".join(re.findall(r"allowed\+?='([^']*)'", script))
    assert allowed and "pyproject" not in allowed
    assert script.index('grep -vE "$allowed"') < script.index("gh pr merge")
    assert script.index("gh run list --workflow desktop.yml --branch main") < script.index("gh pr merge")
    assert script.index("gh pr merge") < script.rindex("gh workflow run gate.yml --ref main")
    # A warning of a retried call goes to stderr, out of the answer a caller reads into a variable.
    assert 'echo "::warning::gh failed (try $try): $*" >&2' in script
    for started in ("gate.yml", "desktop.yml"):
        triggers = _workflow(started)[True]
        assert isinstance(triggers, dict)
        assert "workflow_dispatch" in triggers
