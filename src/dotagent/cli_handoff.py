"""``dotx run`` handed to the daemon of this Dot home: the daemon starts the run, in its own Chrome and its tabs as any
run of its pages, and the command follows it over the daemon's API and prints what a run of its own process prints.

A run that a ``dotx`` command drove in its own process held Dot's Chrome profile for that process alone, so its browser
work waited on the daemon's chats and theirs on it, and its screen was not shown on the pages ("Working elsewhere").
Handed to the daemon, it shares the daemon's browser and is shown as any other. With no daemon of this home (no token
yet, nothing listening at ``[server]``), ``dotx run`` drives the run in its own process, as it does on a Mac without a
daemon (``dotagent.cli_run``).

What stays as it was: the run's source is ``cli``; with ``--no-input`` (or no terminal) every approval it asks is
denied and every question it waits on goes unanswered, by the terminal (``cli``); Ctrl-C stops the run, and a second
one quits. A closed terminal or SIGTERM stops it too: the daemon drives it, and nothing would follow it any more.
"""

import asyncio
import signal
import sys
from collections.abc import AsyncIterator

import httpx
from pydantic import TypeAdapter, ValidationError

from dotagent.cli_campaign import render_first_session_end
from dotagent.cli_commands import RunCommand
from dotagent.cli_daemon import probe_daemon, prove_daemon
from dotagent.cli_ui import TerminalUi, UiOptions, print_result
from dotagent.config import ServerConfig
from dotagent.core.errors import ConfigError, RunConflict, ServiceError
from dotagent.core.logs import get_logger
from dotagent.core.paths import token_path
from dotagent.core.types import FrozenModel
from dotagent.server.guard import read_token
from dotagent.server.links import base_url, health_url
from dotagent.server.views import CampaignDetail, DecisionRequest, RunDetail, StartRunRequest
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord

log = get_logger(__name__)

# How long a call of the daemon's API may take, and how long its stream of a run may say nothing: it sends a keepalive
# every 15 seconds.
_CALL_TIMEOUT_S: float = 15.0
_STREAM_QUIET_S: float = 60.0
_BODY_CHARS: int = 300
_CONFLICT: int = 409
_FINISHED: str = "run_finished"
_DECIDED: str = "approval_decided"
_DATA: str = "data:"
# How long the command waits before it asks again for the stream of a run that is at work, when the stream ended before
# the run did: a daemon that is stopping ends each stream at once until it has stopped the run.
_REASK_S: float = 0.5
_RUN: TypeAdapter[RunRecord] = TypeAdapter(RunRecord)


class DaemonLink(FrozenModel):
    """The daemon of this Dot home, proven to hold its token: where it listens and the token its API takes."""

    host: str
    port: int
    token: str


def _clip(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _BODY_CHARS else f"{flat[: _BODY_CHARS - 1]}…"


async def daemon_of_this_home(server: ServerConfig) -> DaemonLink | None:
    """The daemon of this Dot home, or ``None`` when none runs: there is no API token yet (no daemon ever started
    here), or nothing listens at ``[server]``'s address. What answers there must prove that it holds this home's token
    before it is sent anything (:func:`prove_daemon`), and its error is raised when it does not: another program, or a
    daemon of another home, holds the address."""
    path = token_path()
    if not await asyncio.to_thread(path.exists):
        return None
    if await probe_daemon(health_url(server.host, server.port)) is None:
        return None
    token: str = await asyncio.to_thread(read_token, path)
    async with httpx.AsyncClient(timeout=_CALL_TIMEOUT_S, trust_env=False) as client:
        await prove_daemon(client, server.host, server.port, token)
    return DaemonLink(host=server.host, port=server.port, token=token)


def handed_request(command: RunCommand, goal: str) -> StartRunRequest:
    """What ``POST /api/runs`` is asked for the run of ``command``: what a run of the command's own process starts
    with, its source ``cli`` included. A goal the API refuses (past its length) is refused here, with why."""
    try:
        return StartRunRequest(
            goal=goal,
            mode=command.mode,
            model=command.model,
            thread_id=command.thread,
            effort=command.effort,
            approvals=command.approvals,
            goal_mode=command.goal_mode,
            standing=command.standing,
            browser=command.browser if command.standing else None,
            source="cli",
        )
    except ValidationError as exc:
        raise ConfigError(
            "the run cannot be handed to the Dot daemon as asked",
            {"errors": _clip(str(exc)), "goal_chars": len(goal)},
        ) from exc


class DaemonApi:
    """The calls ``dotx run`` makes of the daemon's API for a run it handed to it."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client: httpx.AsyncClient = client

    async def _call(self, method: str, path: str, body: FrozenModel | None) -> httpx.Response:
        content: bytes | None = None if body is None else body.model_dump_json().encode("utf-8")
        headers: dict[str, str] = {} if body is None else {"Content-Type": "application/json"}
        try:
            response: httpx.Response = await self._client.request(method, path, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceError(
                "the Dot daemon did not answer the call",
                {"method": method, "path": path, "error": repr(exc), "fix": "'dotx service status' says how it is"},
            ) from exc
        if response.status_code == _CONFLICT:
            raise RunConflict(
                "the Dot daemon refused the call in the state the run is in",
                {"method": method, "path": path, "body": _clip(response.text)},
            )
        if response.status_code >= 300:
            raise ServiceError(
                "the Dot daemon refused the call",
                {"method": method, "path": path, "status": response.status_code, "body": _clip(response.text)},
            )
        return response

    @staticmethod
    def _read[T](adapter: TypeAdapter[T], response: httpx.Response) -> T:
        try:
            return adapter.validate_json(response.content)
        except ValidationError as exc:
            raise ServiceError(
                "the Dot daemon's answer cannot be read; a daemon of another version answers so, and "
                "'dotx service restart' restarts it",
                {"url": str(response.url), "status": response.status_code, "body": _clip(response.text)},
            ) from exc

    async def start(self, request: StartRunRequest) -> RunRecord:
        return self._read(_RUN, await self._call("POST", "/api/runs", request))

    async def detail(self, run_id: str) -> RunDetail:
        return self._read(TypeAdapter(RunDetail), await self._call("GET", f"/api/runs/{run_id}", None))

    async def stop(self, run_id: str) -> None:
        await self._call("POST", f"/api/runs/{run_id}/cancel", None)

    async def approval(self, approval_id: str) -> ApprovalRecord:
        return self._read(TypeAdapter(ApprovalRecord), await self._call("GET", f"/api/approvals/{approval_id}", None))

    async def decide(self, approval_id: str, approve: bool, note: str) -> None:
        decision: DecisionRequest = DecisionRequest(approve=approve, note=note, remember=False, by="cli")
        await self._call("POST", f"/api/approvals/{approval_id}/decision", decision)

    async def campaign(self, campaign_id: str) -> CampaignDetail:
        return self._read(TypeAdapter(CampaignDetail), await self._call("GET", f"/api/campaigns/{campaign_id}", None))

    async def events(self, run_id: str, after: int) -> AsyncIterator[EventRecord]:
        """The events of ``run_id`` and of the runs it delegated to, stored after ``after`` and then live, until the
        stream ends (``GET /api/runs/{id}/stream``)."""
        path: str = f"/api/runs/{run_id}/stream"
        timeout: httpx.Timeout = httpx.Timeout(_CALL_TIMEOUT_S, read=_STREAM_QUIET_S)
        try:
            async with self._client.stream("GET", path, params={"after": after}, timeout=timeout) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise ServiceError(
                        "the Dot daemon refused the stream of the run",
                        {"path": path, "status": response.status_code, "body": _clip(response.text)},
                    )
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith(_DATA):
                        data.append(line.removeprefix(_DATA).removeprefix(" "))
                    elif line == "" and data:
                        yield _event_of("\n".join(data), path)
                        data = []
        except httpx.HTTPError as exc:
            raise ServiceError(
                "the Dot daemon stopped answering while it drove the run",
                {"run_id": run_id, "after": after, "error": repr(exc), "fix": "the run's page shows how it went on"},
            ) from exc


def _event_of(data: str, path: str) -> EventRecord:
    """The event a frame of the daemon's stream of a run carries."""
    try:
        return EventRecord.model_validate_json(data)
    except ValidationError as exc:
        raise ServiceError(
            "an event of the Dot daemon's stream cannot be read; a daemon of another version sends so, and "
            "'dotx service restart' restarts it",
            {"path": path, "data": _clip(data)},
        ) from exc


class DaemonDesk:
    """The terminal's decisions on a run the daemon drives (``cli_ui.DecisionDesk``): stored through its API as the
    terminal's (``cli``), and a decision made without the terminal is learnt from the run's stream."""

    def __init__(self, api: DaemonApi) -> None:
        self._api: DaemonApi = api
        self._decided: set[str] = set()
        self._waits: dict[str, asyncio.Event] = {}

    def saw_decided(self, approval_id: str) -> None:
        """The stream told that ``approval_id`` was decided."""
        self._decided.add(approval_id)
        waiting: asyncio.Event | None = self._waits.get(approval_id)
        if waiting is not None:
            waiting.set()

    async def settled(self, approval_id: str) -> None:
        if approval_id in self._decided:
            return
        await self._waits.setdefault(approval_id, asyncio.Event()).wait()

    async def record(self, approval_id: str) -> ApprovalRecord:
        return await self._api.approval(approval_id)

    async def resolve(self, approval_id: str, approve: bool, note: str) -> None:
        await self._api.decide(approval_id, approve, note)


async def _read_events(api: DaemonApi, desk: DaemonDesk, run_id: str, queue: asyncio.Queue[EventRecord | None]) -> None:
    """Put the run's events on ``queue`` until its end, then ``None``, also when reading failed. A stream that ended
    before the run did (the daemon was stopping) is asked again from the last event read while the run is at work."""
    after: int = 0
    try:
        while True:
            finished: bool = False
            async for event in api.events(run_id, after):
                after = max(after, event.id)
                approval_id: object = event.data.get("approval_id")
                if event.kind == _DECIDED and isinstance(approval_id, str):
                    desk.saw_decided(approval_id)
                finished = finished or (event.kind == _FINISHED and event.run_id == run_id)
                queue.put_nowait(event)
            if finished or not (await api.detail(run_id)).active:
                return
            await asyncio.sleep(_REASK_S)
    finally:
        queue.put_nowait(None)


async def _ask_stop(api: DaemonApi, run_id: str) -> None:
    """Ask the daemon to stop the run; a run that ended meanwhile is said so in the log, as nothing is left to stop."""
    try:
        await api.stop(run_id)
    except RunConflict as exc:
        log.info("handed_run_stop_refused", run_id=run_id, reason=exc.message, context=exc.context)


async def hand_run(link: DaemonLink, server: ServerConfig, command: RunCommand, goal: str, options: UiOptions) -> int:
    """Start the run of ``command`` on the daemon of ``link``, show its events as they come, then print its answer,
    what it needs of the user, the questions it left and its campaign, as ``dotx run`` does for a run of its own; 0 when
    it ended done. ``server`` is the ``[server]`` the link was found at. The run is the daemon's from its start: Ctrl-C
    asks the daemon to stop it (a second one quits), as do SIGHUP and SIGTERM, after which nothing follows it."""
    request: StartRunRequest = handed_request(command, goal)
    async with httpx.AsyncClient(
        base_url=base_url(link.host, link.port),
        headers={"Authorization": f"Bearer {link.token}"},
        timeout=_CALL_TIMEOUT_S,
        trust_env=False,
    ) as client:
        api: DaemonApi = DaemonApi(client)
        run: RunRecord = await api.start(request)
        log.info("run_handed_to_daemon", run_id=run.id, thread_id=run.thread_id, port=link.port)
        desk: DaemonDesk = DaemonDesk(api)
        ui: TerminalUi = TerminalUi(desk, server, options)
        queue: asyncio.Queue[EventRecord | None] = asyncio.Queue()
        reader: asyncio.Task[None] = asyncio.create_task(_read_events(api, desk, run.id, queue))
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        main_task: asyncio.Task[object] | None = asyncio.current_task()
        stops: list[asyncio.Task[None]] = []
        presses: list[int] = []

        def on_interrupt() -> None:
            presses.append(1)
            if len(presses) == 1:
                sys.stderr.write("\nstopping the run (press Ctrl-C again to quit; Dot records the stop)\n")
                stops.append(asyncio.create_task(_ask_stop(api, run.id)))
            elif main_task is not None:
                main_task.cancel()

        def on_end() -> None:
            # Nothing is written: after SIGHUP the terminal is gone.
            stops.append(asyncio.create_task(_ask_stop(api, run.id)))

        loop.add_signal_handler(signal.SIGINT, on_interrupt)
        loop.add_signal_handler(signal.SIGHUP, on_end)
        loop.add_signal_handler(signal.SIGTERM, on_end)
        try:
            while (event := await queue.get()) is not None:
                await ui.handle(event)
            await reader
            await asyncio.gather(*stops)
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGHUP)
            loop.remove_signal_handler(signal.SIGTERM)
            reader.cancel()
        ended: RunDetail = await api.detail(run.id)
        print_result(ended.run)
        ui.show_open_ends(ui.needs_from_user, ended.questions)
        if ended.run.campaign_id is not None:
            campaign: CampaignDetail = await api.campaign(ended.run.campaign_id)
            sys.stderr.write(f"{render_first_session_end(campaign.record)}\n")
        if ended.lessons is not None:
            ui.show_lessons(ended.lessons)
    return 0 if ended.run.status == "done" else 1
