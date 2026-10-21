"""The release publishes only what the gate passed again (item 457): the publish job needs the tag's job and the
gate's, keeps the environment the PyPI publisher names, and is the only job with an identity token; the gate can be
called. Read from the workflow files themselves, so an edit that drops one is caught before a tag is pushed."""

from pathlib import Path

import yaml

from dotagent.core.types import JsonObject

_WORKFLOWS: Path = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _workflow(name: str) -> JsonObject:
    loaded: object = yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), name
    return loaded


def test_nothing_is_published_unless_the_tag_and_the_gate_passed() -> None:
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
