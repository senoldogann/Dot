"""The routes of the reactions on Dot's messages (item 586), on a real server over a scripted model: a reaction put on
a message of a run that ended, listed by message and by chat, put again and taken back; the refusals (no such run, a
key that is no message, a reaction the daemon does not know, a message with no words, the answer of a run at work, no
token); the event every page hears; the settings, which hold at once and are kept in the config file, and are left as
they were when the file cannot be changed; and the run's page, which says whether a hold waits for the owner.

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from reaction_stand_ins import ANSWER, SAID, Session, finish, open_session, says

from dotagent.agent.reaction_words import REACTION_ADDED_EVENT, REACTION_REMOVED_EVENT
from dotagent.config import load_default_config
from dotagent.core.paths import config_path
from dotagent.core.types import JsonObject
from dotagent.evals.types import ScriptedTurn
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.stream import LIFECYCLE_KINDS
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "reaction-routes-token"
_WAIT_S: float = 30.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(session: Session) -> AsyncIterator[httpx.AsyncClient]:
    """A real server over the session's runtime, run manager and service of the reactions, and a client of it that
    holds the token."""
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=session.runtime,
        manager=session.manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
        reactions=session.reactions,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as client:
            yield client
    finally:
        server.should_exit = True
        stopping.set()
        await serving


def _message(run: RunRecord, key: str = "answer") -> str:
    return f"/api/runs/{run.id}/messages/{key}/reactions"


async def test_a_reaction_is_put_listed_put_again_and_taken_back(login_env: dict[str, str]) -> None:
    async with open_session(login_env, [finish(ANSWER)], follow_ups=False) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.ended(run.id)
        put: httpx.Response = await client.post(_message(run), json={"reaction": "good"})
        assert put.status_code == 201, put.text
        body: JsonObject = put.json()
        assert (body["effect"], body["said"], body["run_id"]) == ("feedback", "", None)
        assert [(row["run_id"], row["message"], row["reaction"]) for row in body["reactions"]] == [
            (run.id, "answer", "good")
        ]
        # The same reaction again is the reaction there is: nothing is done twice.
        again: httpx.Response = await client.post(_message(run), json={"reaction": "good"})
        assert (again.status_code, again.json()["effect"]) == (200, "unchanged")
        await client.post(_message(run), json={"reaction": "remember"})
        listed: httpx.Response = await client.get(_message(run))
        assert [row["reaction"] for row in listed.json()] == ["good", "remember"]
        chat: httpx.Response = await client.get(f"/api/threads/{run.thread_id}/reactions")
        assert [(row["message"], row["reaction"], row["thread_id"]) for row in chat.json()] == [
            ("answer", "good", run.thread_id),
            ("answer", "remember", run.thread_id),
        ]
        taken: httpx.Response = await client.delete(f"{_message(run)}/good")
        assert (taken.status_code, taken.json()["effect"]) == (200, "taken_back")
        assert [row["reaction"] for row in taken.json()["reactions"]] == ["remember"]
        nothing: httpx.Response = await client.delete(f"{_message(run)}/good")
        assert (nothing.status_code, nothing.json()["effect"]) == (200, "unchanged")
        assert [row["reaction"] for row in (await client.get(_message(run))).json()] == ["remember"]


async def test_a_reaction_on_a_step_of_a_run_is_kept_by_the_run_and_its_step(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.model.started(2)
        put: httpx.Response = await client.post(_message(run, "step-1"), json={"reaction": "good"})
        assert (put.status_code, put.json()["effect"]) == (201, "told")
        assert (await client.get(_message(run, "answer"))).json() == []
        assert [row["message"] for row in (await client.get(f"/api/threads/{run.thread_id}/reactions")).json()] == [
            "step-1"
        ]
        session.model.open(2)
        await session.ended(run.id)


async def test_what_cannot_be_reacted_to_is_refused_with_the_reason(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.model.started(2)
        unknown: httpx.Response = await client.post(
            "/api/runs/run_nope/messages/answer/reactions", json={"reaction": "good"}
        )
        assert unknown.status_code == 404
        # The answer of a run that works is not there yet; a step it has not said is nothing.
        early: httpx.Response = await client.post(_message(run), json={"reaction": "good"})
        assert (early.status_code, early.json()["message"]) == (422, "a run has no answer until it ends")
        missing: httpx.Response = await client.post(_message(run, "step-9"), json={"reaction": "good"})
        assert (missing.status_code, missing.json()["message"]) == (404, "the run said nothing in this step")
        # What is no key of a message, and what is no reaction, never reach the service.
        for key in ("step-x", "steps-1", "first", "step-1234567"):
            refused: httpx.Response = await client.post(_message(run, key), json={"reaction": "good"})
            assert refused.status_code == 422, key
        for body in ({"reaction": "angry"}, {"reaction": ""}, {}, {"reaction": "good", "also": "love"}):
            refused = await client.post(_message(run, "step-1"), json=body)
            assert refused.status_code == 422, body
        assert (await client.delete(f"{_message(run, 'step-1')}/angry")).status_code == 422
        assert (await client.get("/api/threads/thr_nope/reactions")).status_code == 404
        assert (await client.get("/api/runs/run_nope/messages/answer/reactions")).status_code == 404
        session.model.open(2)
        await session.ended(run.id)
        assert await session.runtime.store.thread_reactions(run.thread_id) == []


async def test_a_step_has_one_key_so_one_reaction_on_it_starts_one_follow_up(login_env: dict[str, str]) -> None:
    """Item 612 (the final review): ``step-1``, ``step-01`` and ``step-0001`` were three messages, each with a claim of
    its own, and one 👎 on one sentence of a run started a follow-up run for each (as many as the hour allowed)."""
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves."), finish("Reconsidered: the beds face east.")]
    async with open_session(login_env, turns) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.ended(run.id)
        first: httpx.Response = await client.post(_message(run, "step-1"), json={"reaction": "wrong"})
        assert (first.status_code, first.json()["effect"]) == (201, "follow_up")
        for key in ("step-01", "step-0001", "step-001", "step-0", "step-00"):
            refused: httpx.Response = await client.post(_message(run, key), json={"reaction": "wrong"})
            assert refused.status_code == 422, key
            assert (await client.get(_message(run, key))).status_code == 422, key
            assert (await client.delete(f"{_message(run, key)}/wrong")).status_code == 422, key
        follow: str = first.json()["run_id"]
        await session.ended(follow)
        # One follow-up in the chat, and one key on the chat's reactions: the one the page writes.
        assert [one.id for one in await session.runtime.store.list_runs(10, run.thread_id)] == [follow, run.id]
        listed: httpx.Response = await client.get(f"/api/threads/{run.thread_id}/reactions")
        assert [(row["message"], row["reaction"], row["follow_up_run_id"]) for row in listed.json()] == [
            ("step-1", "wrong", follow)
        ]


async def test_the_routes_answer_only_with_the_token(login_env: dict[str, str]) -> None:
    async with open_session(login_env, [finish(ANSWER)]) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.ended(run.id)
        base: str = str(client.base_url)
        async with httpx.AsyncClient(base_url=base) as stranger:
            put: httpx.Response = await stranger.post(_message(run), json={"reaction": "good"})
            assert put.status_code == 401
            assert (await stranger.get(_message(run))).status_code == 401
            assert (await stranger.delete(f"{_message(run)}/good")).status_code == 401
            assert (await stranger.get("/api/reactions/settings")).status_code == 401
            assert (
                await stranger.post("/api/reactions/settings", json={"key": "wrong", "value": "feedback"})
            ).status_code == 401
        async with httpx.AsyncClient(base_url=base, headers={"Authorization": "Bearer not-the-token"}) as wrong:
            assert (await wrong.post(_message(run), json={"reaction": "good"})).status_code == 401
        assert await session.runtime.store.thread_reactions(run.thread_id) == []


async def test_every_page_hears_a_reaction_put_and_taken_back(login_env: dict[str, str]) -> None:
    assert {REACTION_ADDED_EVENT, REACTION_REMOVED_EVENT} <= LIFECYCLE_KINDS
    async with open_session(login_env, [finish(ANSWER)], follow_ups=False) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.ended(run.id)
        heard: list[str] = []
        async with client.stream("GET", "/api/stream") as stream:
            await client.post(_message(run), json={"reaction": "love"})
            await client.delete(f"{_message(run)}/love")
            async with asyncio.timeout(_WAIT_S):
                async for line in stream.aiter_lines():
                    if line.startswith("event: reaction_"):
                        heard.append(line.removeprefix("event: "))
                    if len(heard) == 2:
                        break
        assert heard == [REACTION_ADDED_EVENT, REACTION_REMOVED_EVENT]


async def test_the_settings_hold_at_once_and_are_kept_in_the_config_file(login_env: dict[str, str]) -> None:
    async with (
        open_session(login_env, [finish(ANSWER), finish("Reconsidered.")]) as session,
        _served(session) as client,
    ):
        shown: httpx.Response = await client.get("/api/reactions/settings")
        assert shown.json() == load_default_config().reactions.model_dump(mode="json")
        off: httpx.Response = await client.post("/api/reactions/settings", json={"key": "follow_ups", "value": False})
        assert (off.status_code, off.json()["follow_ups"]) == (200, False)
        quiet: httpx.Response = await client.post("/api/reactions/settings", json={"key": "good", "value": "follow_up"})
        assert quiet.json()["good"] == "follow_up"
        kept: JsonObject = tomllib.loads(config_path().read_text(encoding="utf-8"))["reactions"]
        assert kept == {"follow_ups": False, "good": "follow_up"}
        run: RunRecord = await session.start()
        await session.ended(run.id)
        # With the switch off a 👎 starts nothing; the next press after it is on starts the run.
        pressed: httpx.Response = await client.post(_message(run), json={"reaction": "wrong"})
        assert (pressed.json()["effect"], pressed.json()["run_id"]) == ("feedback", None)
        await client.post("/api/reactions/settings", json={"key": "follow_ups", "value": True})
        await client.delete(f"{_message(run)}/wrong")
        again: httpx.Response = await client.post(_message(run), json={"reaction": "wrong"})
        assert again.json()["effect"] == "follow_up"
        await session.ended(again.json()["run_id"])
        assert tomllib.loads(config_path().read_text(encoding="utf-8"))["reactions"] == {
            "follow_ups": True,
            "good": "follow_up",
        }


async def test_a_setting_the_page_sends_wrong_is_refused_and_a_file_that_cannot_be_changed_leaves_it(
    login_env: dict[str, str],
) -> None:
    async with open_session(login_env, [finish(ANSWER)]) as session, _served(session) as client:
        for body in (
            {"key": "follow_ups", "value": "feedback"},
            {"key": "wrong", "value": True},
            {"key": "wrong", "value": "fix_it"},
            {"key": "remember", "value": "feedback"},
            {"key": "follow_ups_per_hour", "value": 5},
            {"key": "wrong"},
        ):
            refused: httpx.Response = await client.post("/api/reactions/settings", json=body)
            assert refused.status_code == 422, body
        # A config file that cannot be read as TOML is not written over: the setting stays as it was.
        path: Path = config_path()
        await asyncio.to_thread(path.write_text, "[reactions\nwrong = ", encoding="utf-8")
        failed: httpx.Response = await client.post(
            "/api/reactions/settings", json={"key": "wrong", "value": "feedback"}
        )
        assert failed.status_code == 422, failed.text
        assert (await client.get("/api/reactions/settings")).json()["wrong"] == "follow_up"
        assert await asyncio.to_thread(path.read_text, encoding="utf-8") == "[reactions\nwrong = "


async def test_the_page_of_a_run_says_whether_a_hold_waits_for_the_owner(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session, _served(session) as client:
        run: RunRecord = await session.start()
        await session.model.started(2)
        assert (await client.get(f"/api/runs/{run.id}")).json()["holding"] is False
        await client.post(_message(run, "step-1"), json={"reaction": "hold"})
        await session.held(run.id)
        assert (await client.get(f"/api/runs/{run.id}")).json()["holding"] is True
        await client.post(f"/api/runs/{run.id}/steer", json={"text": "Go on."})
        await session.ended(run.id)
        assert (await client.get(f"/api/runs/{run.id}")).json()["holding"] is False
