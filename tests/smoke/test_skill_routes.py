"""The API of the learned skills' life (item 585), in the real runtime: the list says which skills rest, why, and which
are pinned, taught or marked; a skill no run read for the days it takes rests by disuse, and the owner pins, rests and
wakes one; the versions of a skill are listed with what each said and put back as a new version that can be put back
in turn; a proposal to merge waits as a card, is accepted into a new version of one skill with the other resting and
kept, or is dismissed; one whose skills changed since is refused. Nothing deletes a skill but the owner's Delete.

No model service is used; the home is a throw-away directory, where the skills are written.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import (
    Playbook,
    PlaybookMeta,
    playbook_name,
    read_meta,
    save_meta,
    taught_name,
    update_playbook,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft
from dotagent.agent.skill_proposals import SkillProposal, save_proposal
from dotagent.config import AppConfig, load_default_config
from dotagent.core.ids import new_id
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.skills.types import SKILL_FILE
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "skill-routes-test-token"
PORT: int = 18769
DAY_S: float = 86400.0
FIRST: str = "Manage a cold email campaign"
SECOND: str = "Track cold email progress"
TAUGHT: str = "Fill the weekly timesheet"


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _home(login_env: dict[str, str]) -> AsyncIterator[Home]:
    with scratch_home() as home, eval_environment(home, {}):
        config: AppConfig = eval_config(load_default_config())
        config = config.model_copy(update={"agent": config.agent.model_copy(update={"playbooks": True})})
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


@asynccontextmanager
async def _client(home: Home) -> AsyncIterator[httpx.AsyncClient]:
    context: ServerContext = ServerContext(
        runtime=home.runtime,
        manager=home.manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=PORT,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(context)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


def _draft(kind: str, *steps: str) -> PlaybookDraft:
    return PlaybookDraft(
        kind=kind,
        description=f"{kind}: what is done and where.",
        when=f"A goal that asks for {kind.lower()}.",
        steps=list(steps),
        parameters=["<list>: the contacts"],
    )


def _playbook(
    kind: str, run: str, *steps: str, tainted: bool = False, taught: bool = False, at: float | None = None
) -> Playbook:
    return Playbook(
        name=taught_name(kind) if taught else playbook_name(kind),
        kind=kind,
        host="mail.example.com",
        draft=_draft(kind, *steps),
        run_id="" if taught else run,
        agent_id=None,
        goal=f"Goal of {run}",
        written_at=time.time() if at is None else at,
        acting_steps=len(steps),
        tainted=tainted and not taught,
        taught=taught,
    )


def _write(kind: str, run: str, *steps: str, **more: bool | float | None) -> str:
    """A learned skill in the throw-away home. Returns its name."""
    playbook: Playbook = _playbook(kind, run, *steps, **more)  # type: ignore[arg-type]
    write_playbook(playbooks_dir(), playbook)
    return playbook.name


def _age(name: str, days: float, **more: object) -> None:
    """Make a skill look as if it was written, last rewritten and last read ``days`` ago (or never read)."""
    folder: Path = playbooks_dir() / name
    then: float = time.time() - days * DAY_S
    meta: PlaybookMeta = read_meta(folder)
    save_meta(folder, meta.model_copy(update={"written_at": then, "updated_at": then, **more}))


def _by_name(body: dict[str, object]) -> dict[str, dict[str, object]]:
    items: object = body["items"]
    assert isinstance(items, list)
    return {str(item["name"]): item for item in items}


async def test_the_list_says_which_skills_rest_and_why_and_the_owner_pins_rests_and_wakes(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env) as home, _client(home) as client:
        fresh: str = _write(FIRST, "run-1", "Open the inbox.")
        old: str = _write(SECOND, "run-2", "Count the replies.", tainted=True)
        taught: str = _write(TAUGHT, "", "Open the sheet.", taught=True)
        _age(old, 70)
        _age(taught, 70)
        listed = await client.get("/api/skills/learned")
        assert listed.status_code == 200 and listed.json()["rest_days"] == 60
        shown = _by_name(listed.json())
        assert (shown[fresh]["resting"], shown[fresh]["rest_reason"], shown[fresh]["version"]) == (False, None, 1)
        assert (shown[old]["resting"], shown[old]["rest_reason"], shown[old]["tainted"]) == (True, "disuse", True)
        # A skill the owner taught rests too, and is never retired for it.
        assert (shown[taught]["resting"], shown[taught]["taught"], shown[taught]["retired"]) == (True, True, False)

        # Pin: it wakes and never rests. Taking the pin off leaves it awake, its days counted from the pin.
        pinned = await client.put(f"/api/skills/learned/{old}/pin", json={"pinned": True})
        assert pinned.status_code == 200
        assert (_by_name(pinned.json())[old]["pinned"], _by_name(pinned.json())[old]["resting"]) == (True, False)
        unpinned = await client.put(f"/api/skills/learned/{old}/pin", json={"pinned": False})
        assert (_by_name(unpinned.json())[old]["pinned"], _by_name(unpinned.json())[old]["resting"]) == (False, False)

        # Rest by hand, then wake it.
        rested = await client.post(f"/api/skills/learned/{fresh}/rest")
        assert (_by_name(rested.json())[fresh]["resting"], _by_name(rested.json())[fresh]["rest_reason"]) == (
            True,
            "owner",
        )
        woken = await client.post(f"/api/skills/learned/{fresh}/wake")
        assert _by_name(woken.json())[fresh]["resting"] is False

        # A skill that is not there is not found, and a pin names its state.
        assert (await client.post("/api/skills/learned/learned-no-such-skill/rest")).status_code == 404
        assert (await client.put(f"/api/skills/learned/{fresh}/pin", json={"pinned": "yes"})).status_code == 422
        # Delete is the owner's alone and works on a skill that rests.
        deleted = await client.delete(f"/api/skills/learned/{taught}")
        assert deleted.status_code == 200 and taught not in _by_name(deleted.json())


async def test_the_versions_of_a_skill_are_listed_and_a_restore_is_a_new_version_that_can_be_put_back(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env) as home, _client(home) as client:
        name: str = _write(FIRST, "run-1", "Open the inbox.", "Send the first mail.")
        update_playbook(
            playbooks_dir(), name, _playbook(FIRST, "run-2", "Open the inbox.", "Send the mails.", tainted=True)
        )
        listed = await client.get(f"/api/skills/learned/{name}/versions")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert [(item["note"]["n"], item["note"]["run_id"], item["current"]) for item in items] == [
            (1, "run-1", False),
            (2, "run-2", True),
        ]
        assert items[0]["text"]["steps"] == ["Open the inbox.", "Send the first mail."]
        assert items[1]["note"]["tainted"] is True and items[0]["note"]["tainted"] is False

        # Version 1 is put back as version 3, with the mark it had.
        restored = await client.post(f"/api/skills/learned/{name}/restore", json={"version": 1})
        assert restored.status_code == 200
        body = _by_name(restored.json())[name]
        assert (body["version"], body["tainted"]) == (3, False)
        steps: list[str] = [
            step
            for item in (await client.get(f"/api/skills/learned/{name}/versions")).json()["items"]
            for step in item["text"]["steps"]
        ]
        assert steps.count("Send the first mail.") == 2 and steps.count("Send the mails.") == 1
        # The version it replaced is still there, and the one that is the skill's own cannot be put back.
        again = await client.post(f"/api/skills/learned/{name}/restore", json={"version": 2})
        assert (
            again.status_code == 200
            and _by_name(again.json())[name]["version"] == 4
            and _by_name(again.json())[name]["tainted"] is True
        )
        assert (await client.post(f"/api/skills/learned/{name}/restore", json={"version": 4})).status_code == 422
        assert (await client.post(f"/api/skills/learned/{name}/restore", json={"version": 9})).status_code == 404
        assert (await client.post(f"/api/skills/learned/{name}/restore", json={"version": 0})).status_code == 422
        assert (await client.get("/api/skills/learned/learned-no-such-skill/versions")).status_code == 404


async def test_a_merge_proposal_waits_as_a_card_and_a_yes_writes_what_was_shown_and_a_no_is_remembered(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env) as home, _client(home) as client:
        one: str = _write(FIRST, "run-1", "Open the inbox.", "Send the first mail.")
        two: str = _write(SECOND, "run-2", "Count the replies.")
        root: Path = playbooks_dir()
        merged: PlaybookDraft = _draft(
            "Run a cold email campaign", "Open the inbox.", "Send the mails.", "Count replies."
        )

        def proposal(names: tuple[str, ...]) -> SkillProposal:
            made: SkillProposal = SkillProposal(
                id=new_id("skp"),
                kind="merge",
                names=names,
                draft=merged,
                reason="the same procedure",
                words=None,
                bases={name: read_meta(root / name).version for name in names},
                tainted=False,
                created_at=time.time(),
            )
            save_proposal(root, made)
            return made

        waiting: SkillProposal = proposal((one, two))
        shown = await client.get("/api/skills/proposals")
        assert shown.status_code == 200
        (card,) = shown.json()["items"]
        assert (card["stale"], card["proposal"]["id"], card["proposal"]["names"]) == (None, waiting.id, [one, two])
        assert [skill["steps"] for skill in card["current"]] == [
            ["Open the inbox.", "Send the first mail."],
            ["Count the replies."],
        ]

        accepted = await client.post(f"/api/skills/proposals/{waiting.id}/accept")
        assert accepted.status_code == 200, accepted.text
        listed = _by_name(accepted.json())
        assert (listed[one]["version"], listed[one]["kind"]) == (2, "Run a cold email campaign")
        assert (listed[two]["resting"], listed[two]["rest_reason"], listed[two]["merged_into"]) == (True, "merged", one)
        assert (await client.get("/api/skills/proposals")).json()["items"] == []
        # Both are kept whole: the first's old file as its version, the other as it was.
        assert (root / one / ".versions" / "1.md").is_file() and (root / two / SKILL_FILE).is_file()
        assert (await client.post(f"/api/skills/proposals/{waiting.id}/accept")).status_code == 404

        # Waking the one a merge put aside makes it its own skill again.
        woken = await client.post(f"/api/skills/learned/{two}/wake")
        assert _by_name(woken.json())[two]["merged_into"] is None and _by_name(woken.json())[two]["resting"] is False

        # A proposal whose skill changed since says so and is refused; a no drops it.
        stale: SkillProposal = proposal((one, two))
        update_playbook(root, two, _playbook(SECOND, "run-3", "Count the replies twice."))
        card = (await client.get("/api/skills/proposals")).json()["items"][0]
        assert card["stale"] == f"the skill {two} changed since this was proposed"
        refused = await client.post(f"/api/skills/proposals/{stale.id}/accept")
        assert refused.status_code == 409 and "changed since this was proposed" in refused.text
        dropped = await client.delete(f"/api/skills/proposals/{stale.id}")
        assert dropped.status_code == 200 and dropped.json()["items"] == []
        assert (await client.delete(f"/api/skills/proposals/{stale.id}")).status_code == 404
        # What is not the shape of a proposal's id is no proposal, and reaches no file.
        assert (await client.delete("/api/skills/proposals/evil")).status_code == 404
        assert (await client.post("/api/skills/proposals/skp_x_zzzzzz/accept")).status_code == 404
