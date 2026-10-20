"""Routes of the MCP servers on Customize › Plugins (items 279 and 316): every server Dot has with where it stands, the
catalogue's services, adding one, connecting one again (signing in again) and removing one, and the page an
authorization server sends the browser back to after a sign-in (``/oauth/callback``, outside ``/api``, so that it needs
no token).

Adding a server tries it first: Dot connects to it once with a sign-in the owner can give (only a server that answers
401 asks for one), keeps it once it answered with its tools, and connects it again as runs do, its tools offered to
the runs that start after. The sign-in happens in the owner's own browser, on the page the add answers with, and comes
back to the address of the page the owner added it from (the Mac's, or the one their phone reaches it at)."""

import asyncio
import html
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import JsonValue, ValidationError

from dotagent.config import McpServerConfig
from dotagent.connections.directory import logo_of_service
from dotagent.core.errors import ConfigError, DotError, NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import connected_servers_path, env_file_path
from dotagent.mcp.catalogue import MCP_SERVICES, McpService
from dotagent.mcp.connected import ConnectedServers, forget_connected, keep_connected, read_connected
from dotagent.mcp.hub import MAX_UNFILTERED_TOOLS, McpHub
from dotagent.mcp.launch import SECRET_REFERENCE
from dotagent.mcp.signin import CALLBACK_PATH, SIGN_IN_WAIT_S, SignIns, forget_sign_in, sign_in_path, signed_in
from dotagent.mcp.types import McpFailure
from dotagent.oauth.accounts import CameBack
from dotagent.secrets import remove_env_value, write_env_value
from dotagent.server.context import ServerContext
from dotagent.server.guard import origin_is_local
from dotagent.server.views import (
    McpAllow,
    McpOfferedTool,
    McpOfferedView,
    McpOrigin,
    McpReconnect,
    McpServerAdd,
    McpServerState,
    McpServersView,
    McpServerView,
    McpServiceView,
    ServiceSecretView,
)
from dotagent.tools.mcp import refresh_mcp_tools

log = get_logger(__name__)

# How long adding a server or signing in waits before it answers the page: until the sign-in page is known or the try
# ended, at most this long; the page asks again until the try ends.
_ANSWER_WITHIN_S: float = 20.0
# What a try that may sign in waits for besides the owner: the server's start and the exchange of the code.
_TRY_MARGIN_S: float = 60.0
_SSE_SUFFIX: str = "/sse"
_URL_SCHEMES: tuple[str, ...] = ("http://", "https://")
_NAME_RULE: str = "a server's name is lowercase letters, digits and dashes, at most 24, starting with a letter"
_TAKEN_NAME: str = "another MCP server has its name"
_PAGE: str = (
    '<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Dot</title></head>\n'
    '<body>\n<h1>{title}</h1>\n<p>{text}</p>\n<p><a href="/#/customize/plugins">Back to '
    "Plugins</a></p>\n</body>\n</html>\n"
)


@dataclass
class _Try:
    """Dot trying a server now, or the last try that failed: what it keeps once the server answers, where it is set up,
    the page the owner signs in on, why the try failed, and a signal for each of those news."""

    config: McpServerConfig
    origin: McpOrigin
    news: asyncio.Event
    task: asyncio.Task[None] | None
    link: str | None
    error: str | None
    # The try failed because the server offers more tools than Dot hands a model at once: the owner can choose some.
    too_many: bool = False

    def running(self) -> bool:
        return self.task is not None and not self.task.done()


def _page(title: str, text: str) -> str:
    return _PAGE.format(title=html.escape(title), text=html.escape(text))


def _address(config: McpServerConfig) -> str:
    if config.url is not None:
        return config.url
    return shlex.join([config.command or "", *config.args])


def _service_of(config: McpServerConfig) -> str | None:
    """The service of the catalogue ``config`` is a server of: the one at its URL, or the one started by its command
    and first arguments (the package), so that a server added by hand wears the service's mark too (item 408)."""
    for service in MCP_SERVICES:
        if service.url is not None and service.url == config.url:
            return service.id
        if service.command is not None and service.command == config.command:
            package: tuple[str, ...] = tuple(arg for arg in service.args if not arg.startswith("-"))
            if package and package == tuple(arg for arg in config.args if not arg.startswith("-"))[: len(package)]:
                return service.id
    return None


def kept_secret_names(service: McpService) -> dict[str, str]:
    """The names Dot keeps a service's secrets under in its ``.env``, by the names the service knows them by:
    ``MCP_<SERVICE>_<NAME>``, so that two services' keys of the same name stay apart (item 408)."""
    stem: str = service.id.upper().replace("-", "_")
    return {secret.name: f"MCP_{stem}_{secret.name}" for secret in service.secrets}


def secret_entries(service: McpService, given: Mapping[str, str]) -> dict[str, str]:
    """What goes into Dot's ``.env`` for the secrets ``given`` for ``service``, by the names Dot keeps them under: every
    secret of the service, none other, none empty. Raises ConfigError otherwise, naming what is missing."""
    kept: dict[str, str] = kept_secret_names(service)
    missing: list[JsonValue] = [name for name in kept if given.get(name, "").strip() == ""]
    unknown: list[JsonValue] = [name for name in sorted(set(given) - set(kept))]
    if missing or unknown:
        raise ConfigError(
            "the service asks for its secrets by name", {"service": service.id, "missing": missing, "unknown": unknown}
        )
    # A value is one line, as the env file's writer takes it (every line boundary str.splitlines knows, not the
    # newline alone): refused here, before any of them is written, so a service with two keys never keeps one alone.
    broken: list[JsonValue] = [name for name in kept if len(given[name].strip().splitlines()) != 1]
    if broken:
        raise ConfigError("a secret is one line", {"service": service.id, "secrets": broken})
    return {kept[name]: given[name].strip() for name in kept}


def service_config(service: McpService) -> McpServerConfig:
    """The server of ``service``, its headers or env referring to the secrets by the names Dot keeps them under."""
    kept: dict[str, str] = kept_secret_names(service)

    def renamed(value: str) -> str:
        return SECRET_REFERENCE.sub(lambda match: "${" + kept.get(match.group(1), match.group(1)) + "}", value)

    if service.url is not None:
        headers: dict[str, str] = {key: renamed(value) for key, value in service.headers.items()}
        return McpServerConfig(name=service.id, url=service.url, transport="http", headers=headers)
    env: dict[str, str] = {key: renamed(value) for key, value in service.env.items()}
    return McpServerConfig(name=service.id, command=service.command, args=list(service.args), env=env)


def requested_service(body: McpServerAdd) -> McpService | None:
    """The service of the catalogue ``body`` names, None when it names none; NotFoundError for one it has not."""
    if body.service is None:
        return None
    service: McpService | None = next((one for one in MCP_SERVICES if one.id == body.service), None)
    if service is None:
        raise NotFoundError("the MCP catalogue has no such service", {"service": body.service})
    return service


def requested_server(body: McpServerAdd) -> McpServerConfig:
    """The server ``body`` asks to add: a service of the catalogue (with its secrets given, item 408), a URL (SSE when
    its path ends in ``/sse``), or a command line. Raises NotFoundError for a service the catalogue does not have,
    ConfigError for the rest."""
    service: McpService | None = requested_service(body)
    if service is not None:
        secret_entries(service, body.secrets)
        return service_config(service)
    if body.name is None or body.address is None or body.address.strip() == "":
        raise ConfigError("name a service of the list, or a server's name and its address", {})
    name: str = body.name.strip()
    address: str = body.address.strip()
    try:
        if address.startswith(_URL_SCHEMES):
            sse: bool = urlsplit(address).path.rstrip("/").endswith(_SSE_SUFFIX)
            return McpServerConfig(name=name, url=address, transport="sse" if sse else "http")
        words: list[str] = shlex.split(address)
        return McpServerConfig(name=name, command=words[0], args=words[1:])
    except ValidationError as error:
        wrong_name: bool = any(part == "name" for issue in error.errors() for part in issue["loc"])
        raise ConfigError(
            _NAME_RULE if wrong_name else "the address is neither a URL nor a command line",
            {"name": name, "address": address},
        ) from error
    except ValueError as error:
        # shlex: an unclosed quote.
        raise ConfigError("the command line cannot be read", {"address": address, "error": str(error)}) from error


def chosen_tools(wanted: tuple[str, ...], offered: tuple[str, ...]) -> list[str]:
    """The allow list for the tools ``wanted`` of a server that lists ``offered``: each once, in the server's order.
    Raises ConfigError for none, more than ``MAX_UNFILTERED_TOOLS``, or a name the server does not list."""
    picked: set[str] = set(wanted)
    missing: list[JsonValue] = [name for name in sorted(picked - set(offered))]
    if missing:
        raise ConfigError("the MCP server does not offer these tools", {"missing": missing})
    if not picked or len(picked) > MAX_UNFILTERED_TOOLS:
        raise ConfigError(
            f"choose between 1 and {MAX_UNFILTERED_TOOLS} tools", {"chosen": len(picked), "limit": MAX_UNFILTERED_TOOLS}
        )
    return [name for name in offered if name in picked]


def mcp_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter()
    hub: McpHub = ctx.runtime.mcp
    reach: frozenset[str] = frozenset(ctx.runtime.config.server.reach)
    sign_ins: SignIns = SignIns()
    # The tries under way, and the last failed try of a server Dot does not have, by server name.
    tries: dict[str, _Try] = {}

    def page_origin(origin: str) -> str:
        if not origin_is_local(origin, ctx.port, reach):
            raise ConfigError("the page's origin is not an address of this server", {"origin": origin})
        return origin

    def known(record: ConnectedServers) -> dict[str, tuple[McpServerConfig, McpOrigin]]:
        """Every server Dot has, by name, with where it is set up: its config's first, then the plugins', the other
        agents', those added on the Plugins page (``record``), and the tries of servers it does not have yet."""
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = {}
        for config in ctx.runtime.config.mcp:
            servers.setdefault(config.name, (config, "config"))
        for config in ctx.runtime.plugins.servers():
            servers.setdefault(config.name, (config, "plugin"))
        recorded: frozenset[str] = frozenset(config.name for config in record.servers)
        for name, config in hub.configs().items():
            servers.setdefault(name, (config, "connected" if name in recorded else "elsewhere"))
        for config in record.servers:
            servers.setdefault(config.name, (config, "connected"))
        for name, attempt in tries.items():
            servers.setdefault(name, (attempt.config, attempt.origin))
        return servers

    async def everything() -> dict[str, tuple[McpServerConfig, McpOrigin]]:
        return known(await asyncio.to_thread(read_connected, connected_servers_path()))

    def view(config: McpServerConfig, origin: McpOrigin) -> McpServerView:
        attempt: _Try | None = tries.get(config.name)
        left: McpFailure | None = next((one for one in hub.failures if one.server == config.name), None)
        failure: str | None = None if left is None else left.error
        state: McpServerState
        error: str | None = None
        if attempt is not None and attempt.running():
            state = "trying" if attempt.link is None else "signing_in"
        elif config.name in hub.connected():
            state = "ready"
        elif not config.enabled:
            state = "disabled"
        else:
            state = "left_out"
            fallback: str = _TAKEN_NAME if origin == "connected" else "Dot did not start it"
            error = attempt.error if attempt is not None and attempt.error is not None else failure or fallback
        service: str | None = _service_of(config)
        return McpServerView(
            name=config.name,
            origin=origin,
            address=_address(config),
            service=service if origin == "connected" else None,
            logo=None if service is None else logo_of_service(service),
            state=state,
            tools=tuple(tool.remote_name for tool in hub.tools if tool.server == config.name)
            if state == "ready"
            else (),
            error=error,
            link=attempt.link if attempt is not None and attempt.running() else None,
            signs_in=config.oauth,
            picks_tools=state == "left_out"
            and not config.allow
            and ((attempt is not None and attempt.too_many) or (left is not None and "limit" in left.detail)),
        )

    async def listing() -> McpServersView:
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        return McpServersView(
            servers=tuple(view(config, origin) for config, origin in servers.values()),
            services=tuple(
                McpServiceView(
                    id=service.id,
                    name=service.name,
                    does=service.does,
                    kind=service.kind,
                    url=service.url,
                    command=None if service.command is None else shlex.join([service.command, *service.args]),
                    secrets=tuple(
                        ServiceSecretView(name=secret.name, label=secret.label, help=secret.help)
                        for secret in service.secrets
                    ),
                    signs_in=service.signs_in,
                    logo=logo_of_service(service.id),
                    added=service.id in servers,
                )
                for service in MCP_SERVICES
            ),
        )

    async def forget_service_secrets(config: McpServerConfig) -> None:
        """The key of a service of the catalogue goes with the service's own server (item 408): the one named as the
        service, not one added by hand at the same address, which may stand beside it."""
        service: McpService | None = next((one for one in MCP_SERVICES if one.id == config.name), None)
        if service is None or not service.secrets or _service_of(config) != service.id:
            return
        for kept_name in kept_secret_names(service).values():
            await asyncio.to_thread(remove_env_value, env_file_path(), kept_name)

    async def keep(attempt: _Try, origin: str) -> None:
        """Try the server once, signing in when it asks, then keep it and connect it as runs do. A try that fails
        leaves a service's key in .env, so that Try again can use it; Remove takes the key out with the server, and an
        add after a failed one writes it anew (item 408)."""
        config: McpServerConfig = attempt.config

        def show(link: str) -> None:
            attempt.link = link
            attempt.news.set()

        try:
            if config.url is None:
                await hub.attempt(config, None)
            else:
                waiting: McpServerConfig = config.model_copy(
                    update={"startup_timeout_s": SIGN_IN_WAIT_S + _TRY_MARGIN_S}
                )
                await hub.attempt(waiting, sign_ins.flow(config, origin, show))
            signs: bool = config.oauth or await asyncio.to_thread(signed_in, sign_in_path(config.name))
            kept: McpServerConfig = config.model_copy(update={"oauth": signs})
            if attempt.origin == "connected":
                await asyncio.to_thread(keep_connected, connected_servers_path(), kept)
            await hub.connect(kept)
            refresh_mcp_tools(ctx.runtime.registry, hub)
            tries.pop(config.name, None)
            log.info("mcp_server_added", server=config.name, origin=attempt.origin, signs_in=signs)
        except DotError as error:
            attempt.error = error.message
            attempt.too_many = "limit" in error.context and not config.allow
            log.warning("mcp_server_not_added", server=config.name, error=error.message, context=error.context)
        except Exception as error:
            # Not one of Dot's own errors: a fault in Dot or in the MCP library, as the hub reports it.
            attempt.error = f"{type(error).__name__}: {error}"
            log.error("mcp_server_try_crashed", server=config.name, exc_info=error)
        finally:
            attempt.news.set()

    async def start(config: McpServerConfig, origin: McpOrigin, page: str) -> McpServerView:
        """Try ``config`` in a task of its own, and answer once the sign-in page is known, the try ended, or after
        ``_ANSWER_WITHIN_S``."""
        current: _Try | None = tries.get(config.name)
        if current is not None and current.running():
            raise RunConflict("Dot is trying this server already", {"server": config.name})
        attempt: _Try = _Try(config=config, origin=origin, news=asyncio.Event(), task=None, link=None, error=None)
        tries[config.name] = attempt
        attempt.task = asyncio.create_task(keep(attempt, page), name=f"mcp-try-{config.name}")
        try:
            await asyncio.wait_for(attempt.news.wait(), _ANSWER_WITHIN_S)
        except TimeoutError:
            log.info("mcp_try_goes_on", server=config.name, waited_s=_ANSWER_WITHIN_S)
        return view(config, origin)

    @router.get("/api/mcp/servers")
    async def list_servers() -> McpServersView:
        """Every MCP server Dot has, where it is set up and where it stands, and the catalogue's services."""
        return await listing()

    @router.post("/api/mcp/servers")
    async def add_server(body: McpServerAdd) -> McpServerView:
        """Add a service of the catalogue or a server by its address: tried first, signed in to when it asks (the
        answer's ``link``), kept and offered to runs once it answered with its tools."""
        page: str = page_origin(body.origin)
        config: McpServerConfig = requested_server(body)
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        found: tuple[McpServerConfig, McpOrigin] | None = servers.get(config.name)
        if found is not None and found[1] != "connected":
            raise RunConflict(_TAKEN_NAME, {"server": config.name, "set_up": found[1]})
        current: _Try | None = tries.get(config.name)
        if current is not None and current.running():
            raise RunConflict("Dot is trying this server already", {"server": config.name})
        service: McpService | None = requested_service(body)
        if service is not None and service.secrets:
            # The secrets go into Dot's .env under the names the server's config refers to (item 408), once nothing
            # stands in the add's way and every one of them is there (secret_entries refuses before any is written).
            for kept_name, value in secret_entries(service, body.secrets).items():
                await asyncio.to_thread(write_env_value, env_file_path(), kept_name, value)
        return await start(config, "connected", page)

    @router.post("/api/mcp/servers/{name}/connect")
    async def connect_again(name: str, body: McpReconnect) -> McpServerView:
        """Connect the server ``name`` again: tried once more, and signed in to again when it asks. One added on the
        Plugins page, or one of Dot's config that signs in (``oauth = true``)."""
        page: str = page_origin(body.origin)
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        found: tuple[McpServerConfig, McpOrigin] | None = servers.get(name)
        if found is None:
            raise NotFoundError("Dot has no MCP server of this name", {"server": name})
        config, origin = found
        if origin != "connected" and not (origin == "config" and config.oauth):
            raise RunConflict(
                "this MCP server is set up elsewhere; connect it again there", {"server": name, "set_up": origin}
            )
        return await start(config, origin, page)

    async def chosen_server(name: str) -> McpServerConfig:
        """The server ``name`` whose tools the owner chooses on the page: one added there (Dot's config keeps its own
        ``allow``)."""
        found: tuple[McpServerConfig, McpOrigin] | None = (await everything()).get(name)
        if found is None:
            raise NotFoundError("Dot has no MCP server of this name", {"server": name})
        if found[1] != "connected":
            raise RunConflict(
                "this MCP server is set up elsewhere; choose its tools with `allow` there",
                {"server": name, "set_up": found[1]},
            )
        return found[0]

    @router.get("/api/mcp/servers/{name}/offered")
    async def offered_tools(name: str) -> McpOfferedView:
        """Every tool the server ``name`` lists, to choose those the model gets from it."""
        config: McpServerConfig = await chosen_server(name)
        tools = await hub.offered(config)
        return McpOfferedView(
            server=name,
            tools=tuple(McpOfferedTool(name=tool.remote_name, description=tool.description) for tool in tools),
            allow=tuple(config.allow),
            limit=MAX_UNFILTERED_TOOLS,
        )

    @router.put("/api/mcp/servers/{name}/allow")
    async def choose_tools(name: str, body: McpAllow) -> McpServerView:
        """Keep the tools chosen as the server's allow list and connect it again with them: they are offered to the
        runs that start after."""
        config: McpServerConfig = await chosen_server(name)
        offered: tuple[str, ...] = tuple(tool.remote_name for tool in await hub.offered(config))
        kept: McpServerConfig = config.model_copy(update={"allow": chosen_tools(body.tools, offered)})
        # Connected first, kept after: a server that does not answer now leaves the choice unwritten, so the owner may
        # choose again, where a kept allow list would take the choice away from the page.
        await hub.connect(kept)
        await asyncio.to_thread(keep_connected, connected_servers_path(), kept)
        refresh_mcp_tools(ctx.runtime.registry, hub)
        # The try that failed for too many tools is over: the server is kept now, with its choice.
        tries.pop(name, None)
        log.info("mcp_tools_chosen", server=name, tools=len(kept.allow))
        return view(kept, "connected")

    @router.delete("/api/mcp/servers/{name}")
    async def remove_server(name: str) -> McpServersView:
        """Remove a server added on the Plugins page, or let go of a try: its tools leave the runs that start after,
        and Dot forgets its address and its sign-in."""
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        found: tuple[McpServerConfig, McpOrigin] | None = servers.get(name)
        if found is None:
            raise NotFoundError("Dot has no MCP server of this name", {"server": name})
        if found[1] != "connected":
            raise RunConflict(
                "this MCP server is set up elsewhere; remove it there", {"server": name, "set_up": found[1]}
            )
        attempt: _Try | None = tries.pop(name, None)
        if attempt is not None and attempt.task is not None and not attempt.task.done():
            attempt.task.cancel()
            await asyncio.wait({attempt.task})
        await hub.disconnect(name)
        refresh_mcp_tools(ctx.runtime.registry, hub)
        await asyncio.to_thread(forget_connected, connected_servers_path(), name)
        await asyncio.to_thread(forget_sign_in, sign_in_path(name))
        await forget_service_secrets(found[0])
        log.info("mcp_server_removed", server=name)
        return await listing()

    @router.get(CALLBACK_PATH)
    async def come_back(
        state: str = "", code: str | None = None, error: str | None = None, iss: str | None = None
    ) -> HTMLResponse:
        """Where the authorization server sends the browser back: the code goes to the sign-in that waits for it, an
        account's of Google or Microsoft (item 422) first, else an MCP server's."""
        came: CameBack | None = await ctx.runtime.accounts.come_back(state, code, error, time.time())
        if came is not None:
            return HTMLResponse(_page(came.title, came.text), status_code=came.status)
        server: str | None = sign_ins.answer(state, code, iss, error)
        if server is None:
            return HTMLResponse(
                _page(
                    "Dot does not wait for this sign-in",
                    "It ended, or Dot started again since. Start it again on Customize › Plugins.",
                ),
                status_code=400,
            )
        if error is not None or code is None:
            return HTMLResponse(
                _page(
                    f"Not signed in to {server}",
                    f"The sign-in ended without a yes ({error or 'no code'}). Start it again on Customize › Plugins.",
                )
            )
        return HTMLResponse(
            _page(
                f"Signed in to {server}",
                "Dot connects to it now and offers its tools to the runs that start next. You can close this tab.",
            )
        )

    return router
