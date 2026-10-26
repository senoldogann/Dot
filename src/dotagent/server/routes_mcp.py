"""Routes of the MCP servers on Customize › Connectors (items 279 and 316): every server Dot has with where it stands,
the catalogue's services, adding one, connecting one again (signing in again) and removing one, and the page an
authorization server sends the browser back to after a sign-in (``/oauth/callback``, outside ``/api``, so that it needs
no token; its page is :mod:`dotagent.server.callback_page`).

Adding a server tries it first: Dot connects to it once with a sign-in the owner can give (only a server that answers
401 asks for one), keeps it once it answered with its tools, and connects it again as runs do, its tools offered to
the runs that start after. The sign-in happens in the owner's own browser, on the page the add answers with, and comes
back to the address of the page the owner added it from (the Mac's, or the one their phone reaches it at)."""

import asyncio
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import JsonValue, ValidationError

from dotagent.config import McpServerConfig
from dotagent.config_edit import set_builtin_server
from dotagent.connections.directory import logo_of_service, mark_of_service
from dotagent.core.errors import ConfigError, DotError, NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import added_servers_path, config_path, connected_servers_path, env_file_path, plugins_dir
from dotagent.help.added import read_added_servers
from dotagent.help.added import remove_server as take_back_added_server
from dotagent.help.elsewhere import dot_server_name
from dotagent.mcp.builtin import builtin_servers, switch_of
from dotagent.mcp.catalogue import MCP_SERVICES, McpService, unversioned
from dotagent.mcp.connected import ConnectedServers, forget_connected, keep_connected, read_connected
from dotagent.mcp.hub import McpHub
from dotagent.mcp.launch import SECRET_REFERENCE
from dotagent.mcp.masking import masked_context, shown_command, shown_url
from dotagent.mcp.signin import CALLBACK_PATH, SIGN_IN_WAIT_S, SignIns, forget_sign_in, sign_in_path, signed_in
from dotagent.mcp.types import McpFailure
from dotagent.oauth.accounts import CameBack, denial_reason
from dotagent.plugins.catalog import read_again
from dotagent.plugins.removal import is_dots_own, move_to_trash, trash_folder
from dotagent.plugins.types import Plugin
from dotagent.secrets import remove_env_value, write_env_value
from dotagent.server.callback_page import Callback, Subject, account_callback, render, status_of
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
    McpSwitch,
    ServiceSecretView,
)
from dotagent.tools.mcp import refresh_mcp_tools

log = get_logger(__name__)

# How long adding a server or signing in waits before it answers the page: until the sign-in page is known or the try
# ended, at most this long; the page asks again until the try ends.
_ANSWER_WITHIN_S: float = 20.0
# What a try that may sign in waits for besides the owner: the server's start and the exchange of the code.
_TRY_MARGIN_S: float = 60.0
# How long the first try of a server started on this Mac may take to start (item 535): npx and uvx fetch its package
# then, which took from 52 seconds to more than five minutes on a cold cache. The server kept keeps its own limit, as a
# daemon start waits for every server; later starts find the package in the cache.
FIRST_START_S: float = 300.0
_SSE_SUFFIX: str = "/sse"
_URL_SCHEMES: tuple[str, ...] = ("http://", "https://")
_NAME_RULE: str = "a server's name is lowercase letters, digits and dashes, at most 24, starting with a letter"
_TAKEN_NAME: str = "another connected app has its name"


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

    def running(self) -> bool:
        return self.task is not None and not self.task.done()


def _answer(callback: Callback) -> HTMLResponse:
    """The page for how a sign-in came out, with the status that says it."""
    return HTMLResponse(render(callback), status_code=status_of(callback.outcome))


def _address(config: McpServerConfig, origin: McpOrigin) -> str:
    """Where the server is, as the page and the API show it: a key put in its URL or in an argument of its command is
    the owner's, and is not given back (item 558). A server that comes with Dot is started on the Python Dot runs on,
    whose path says nothing to the owner: it shows as ``python``."""
    if config.url is not None:
        return shown_url(config.url)
    return shown_command("python" if origin == "builtin" else config.command or "", config.args)


def service_of(config: McpServerConfig) -> str | None:
    """The service of the catalogue ``config`` is a server of: the one at its URL, or the one started by its command
    and first arguments (the package), so that a server added by hand wears the service's mark too (item 408). The
    version written after a package is not compared (item 695): the catalogue pins one, and a server added before it did
    (with ``@latest`` or none) is the service's still."""
    for service in MCP_SERVICES:
        if service.url is not None and service.url == config.url:
            return service.id
        if service.command is not None and service.command == config.command:
            package: tuple[str, ...] = tuple(unversioned(arg) for arg in service.args if not arg.startswith("-"))
            given: tuple[str, ...] = tuple(unversioned(arg) for arg in config.args if not arg.startswith("-"))
            if package and package == given[: len(package)]:
                return service.id
    return None


def _host_of(url: str) -> str:
    """The host of ``url`` with its port: the page names the server by it, and never shows the rest of the address,
    where a key may be."""
    parts = urlsplit(url)
    return parts.netloc.rpartition("@")[2]


def mcp_subject(name: str, config: McpServerConfig | None) -> Subject:
    """The server a sign-in is for, as the callback page names it: the service of the catalogue it is a server of, with
    its mark and the host it is at, else the server's own name (and its host)."""
    service_id: str | None = None if config is None else service_of(config)
    service: McpService | None = next((one for one in MCP_SERVICES if one.id == service_id), None)
    return Subject(
        name=name if service is None else service.name,
        detail=None if config is None or config.url is None else _host_of(config.url),
        mark=None if service_id is None else logo_of_service(service_id),
    )


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


def first_try(config: McpServerConfig) -> McpServerConfig:
    """``config`` as the try that adds it starts it: a server started on this Mac is given :data:`FIRST_START_S` to
    start when its own limit is shorter; a server at a URL is left as it is, as its try waits for the sign-in."""
    if config.command is None or config.startup_timeout_s >= FIRST_START_S:
        return config
    return config.model_copy(update={"startup_timeout_s": FIRST_START_S})


def requested_service(body: McpServerAdd) -> McpService | None:
    """The service of the catalogue ``body`` names, None when it names none; NotFoundError for one it has not."""
    if body.service is None:
        return None
    service: McpService | None = next((one for one in MCP_SERVICES if one.id == body.service), None)
    if service is None:
        raise NotFoundError("the list of apps has no such app", {"service": body.service})
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


def _same_server(kept: McpServerConfig, asked: McpServerConfig) -> bool:
    """Whether ``asked`` is the server ``kept``: at the same address, or started by the same command line."""
    return (kept.url, kept.command, kept.args) == (asked.url, asked.command, asked.args)


def chosen_tools(wanted: tuple[str, ...], offered: tuple[str, ...]) -> list[str]:
    """The allow list for the tools ``wanted`` of a server that lists ``offered``: each once, in the server's order, and
    none when every one is wanted, so that the server offers all of its tools, those it adds later too. Raises
    ConfigError for none, or a name the server does not list."""
    picked: set[str] = set(wanted)
    missing: list[JsonValue] = [name for name in sorted(picked - set(offered))]
    if missing:
        raise ConfigError("the connected app does not offer these actions", {"missing": missing})
    if not picked:
        raise ConfigError("choose at least one action", {"chosen": 0, "offered": len(offered)})
    if picked == set(offered):
        return []
    return [name for name in offered if name in picked]


def mcp_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter()
    hub: McpHub = ctx.runtime.mcp
    reach: frozenset[str] = frozenset(ctx.runtime.config.server.reach)
    sign_ins: SignIns = SignIns()
    # The tries under way, and the last failed try of a server Dot does not have, by server name.
    tries: dict[str, _Try] = {}
    # One switch of a server that comes with Dot at a time, so that two pages do not leave the file and the hub apart.
    switching: asyncio.Lock = asyncio.Lock()

    def page_origin(origin: str) -> str:
        if not origin_is_local(origin, ctx.port, reach):
            raise ConfigError("the page's origin is not an address of this server", {"origin": origin})
        return origin

    def builtin_now(name: str) -> McpServerConfig | None:
        """The server that comes with Dot called ``name`` as Dot starts it now, switched on; ``None`` for a name that
        none has."""
        return next(
            (
                one.model_copy(update={"enabled": True})
                for one in builtin_servers(ctx.runtime.config)
                if one.name == name
            ),
            None,
        )

    def known(record: ConnectedServers) -> dict[str, tuple[McpServerConfig, McpOrigin]]:
        """Every server Dot has, by name, with where it is set up: its config's first, then the plugins', the other
        agents', those added on the Connectors page (``record``), those that come with Dot (item 583), also when
        switched off, and the tries of servers it does not have yet."""
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = {}
        for config in ctx.runtime.config.mcp:
            servers.setdefault(config.name, (config, "config"))
        # A plugin taken out (item 497) is gone from its folder: its servers are not Dot's any more, but for one the
        # hub still runs as the plugin set it up, as when the folder went without Dot, which Remove plugin then stops.
        # A server of the same name the owner added since is theirs, not the plugin's.
        running: dict[str, McpServerConfig] = hub.configs()
        for plugin in ctx.runtime.plugins.plugins:
            for config in plugin.servers:
                if plugin.root.is_dir() or running.get(config.name) == config:
                    servers.setdefault(config.name, (config, "plugin"))
        recorded: frozenset[str] = frozenset(config.name for config in record.servers)
        for name, config in running.items():
            origin: McpOrigin = (
                "builtin" if name in ctx.runtime.builtin_names else "connected" if name in recorded else "elsewhere"
            )
            servers.setdefault(name, (config, origin))
        for config in record.servers:
            servers.setdefault(config.name, (config, "connected"))
        # A server that comes with Dot and is switched off runs nowhere: it is listed for its switch.
        for name in ctx.runtime.builtin_names:
            off: McpServerConfig | None = builtin_now(name)
            if off is not None:
                servers.setdefault(name, (off.model_copy(update={"enabled": False}), "builtin"))
        for name, attempt in tries.items():
            servers.setdefault(name, (attempt.config, attempt.origin))
        return servers

    def plugin_of(server: str) -> str | None:
        """The plugin the server ``server`` comes with, by its name."""
        return next(
            (
                plugin.name
                for plugin in ctx.runtime.plugins.plugins
                if any(one.name == server for one in plugin.servers)
            ),
            None,
        )

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
        service: str | None = service_of(config)
        return McpServerView(
            name=config.name,
            origin=origin,
            plugin=plugin_of(config.name) if origin == "plugin" else None,
            address=_address(config, origin),
            service=service if origin == "connected" else None,
            mark=None if service is None else mark_of_service(service),
            state=state,
            tools=tuple(tool.remote_name for tool in hub.tools if tool.server == config.name)
            if state == "ready"
            else (),
            error=error,
            link=attempt.link if attempt is not None and attempt.running() else None,
            signs_in=config.oauth,
            # A server added on the page that runs may have its tools cut down, and one left out with an allow list may
            # have the list chosen again (it names a tool the server no longer offers, or one whose schema Dot cannot
            # use). The others keep their tools where they were set up.
            picks_tools=origin == "connected" and (state == "ready" or (state == "left_out" and bool(config.allow))),
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
        if service is None or not service.secrets or service_of(config) != service.id:
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
                await hub.attempt(first_try(config), None)
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
            # A try that failed after signing in keeps the sign-in, as one that succeeds is kept with it: Choose tools
            # and Try again connect with it (item 499).
            if not config.oauth and await asyncio.to_thread(signed_in, sign_in_path(config.name)):
                attempt.config = config.model_copy(update={"oauth": True})
            # The context holds the server's URL and arguments and the tail of what it printed, where a key may be.
            log.warning(
                "mcp_server_not_added",
                server=config.name,
                error=error.message,
                context=masked_context(error.context),
            )
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
        if found is None or not _same_server(found[0], config):
            # What signing in to a server of this name gave is that server's: tokens issued for another address are
            # never sent to this one (item 558). A name no server has any more has left its sign-in behind.
            await asyncio.to_thread(forget_sign_in, sign_in_path(config.name))
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
        Connectors page, one of Dot's config that signs in (``oauth = true``), or one that comes with Dot and is on."""
        page: str = page_origin(body.origin)
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        found: tuple[McpServerConfig, McpOrigin] | None = servers.get(name)
        if found is None:
            raise NotFoundError("Dot has no connected app of this name", {"server": name})
        config, origin = found
        if origin == "builtin":
            if not config.enabled:
                raise RunConflict(
                    "this connected app is switched off: switch it on", {"server": name, "set_up": origin}
                )
            config = builtin_now(name) or config
        if origin not in ("connected", "builtin") and not (origin == "config" and config.oauth):
            raise RunConflict(
                "this connected app is set up elsewhere; connect it again there", {"server": name, "set_up": origin}
            )
        return await start(config, origin, page)

    async def chosen_server(name: str) -> McpServerConfig:
        """The server ``name`` whose tools the owner chooses on the page: one added there (Dot's config keeps its own
        ``allow``)."""
        found: tuple[McpServerConfig, McpOrigin] | None = (await everything()).get(name)
        if found is None:
            raise NotFoundError("Dot has no connected app of this name", {"server": name})
        if found[1] != "connected":
            raise RunConflict(
                "this connected app is set up elsewhere; choose its actions there",
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
        )

    @router.put("/api/mcp/servers/{name}/allow")
    async def choose_tools(name: str, body: McpAllow) -> McpServerView:
        """Keep the tools chosen as the server's allow list and connect it again with them: they are offered to the
        runs that start after."""
        config: McpServerConfig = await chosen_server(name)
        current: _Try | None = tries.get(name)
        if current is not None and current.running():
            # A try under way (a sign-in again) keeps the config it began with, and would write it over this choice
            # when it ends; the page hides Choose tools meanwhile, a stale page or a second tab does not (item 558).
            raise RunConflict("Dot is trying this server already", {"server": name})
        offered: tuple[str, ...] = tuple(tool.remote_name for tool in await hub.offered(config))
        kept: McpServerConfig = config.model_copy(update={"allow": chosen_tools(body.tools, offered)})
        # Connected first, kept after: a server that does not answer now leaves the choice unwritten, so the owner may
        # choose again, where a kept allow list would take the choice away from the page.
        await hub.connect(kept)
        await asyncio.to_thread(keep_connected, connected_servers_path(), kept)
        refresh_mcp_tools(ctx.runtime.registry, hub)
        # A try of the server that failed or is under way is over: the server is kept now, with its choice.
        tries.pop(name, None)
        log.info("mcp_tools_chosen", server=name, tools=len(kept.allow))
        return view(kept, "connected")

    @router.put("/api/mcp/servers/{name}/switch")
    async def switch_server(name: str, body: McpSwitch) -> McpServersView:
        """Switch a server that comes with Dot (item 583) on or off: off stops it now, and its tools leave the runs that
        start after; on starts it, and its tools go to the runs that start after. The config file keeps it as
        ``[builtin_mcp]``. One that cannot start, or a config file that cannot be changed, leaves the switch as it
        was."""
        async with switching:
            found: tuple[McpServerConfig, McpOrigin] | None = (await everything()).get(name)
            if found is None:
                raise NotFoundError("Dot has no connected app of this name", {"server": name})
            config, origin = found
            switch = switch_of(name)
            if origin != "builtin" or switch is None:
                raise RunConflict(
                    "this connected app does not come with Dot, so it has no switch", {"server": name, "set_up": origin}
                )
            current: _Try | None = tries.get(name)
            if current is not None and current.running():
                raise RunConflict("Dot is trying this server already", {"server": name})
            if body.on:
                was_on: bool = name in hub.configs()
                # Connected first and kept after: a server that cannot start leaves the file as it was, and a file that
                # cannot be changed leaves the server as it was.
                await hub.connect(builtin_now(name) or config.model_copy(update={"enabled": True}))
                try:
                    await asyncio.to_thread(set_builtin_server, config_path(), switch, True)
                except (ConfigError, OSError):
                    if not was_on:
                        await hub.disconnect(name)
                        refresh_mcp_tools(ctx.runtime.registry, hub)
                    raise
            else:
                await asyncio.to_thread(set_builtin_server, config_path(), switch, False)
                await hub.disconnect(name)
            refresh_mcp_tools(ctx.runtime.registry, hub)
            tries.pop(name, None)
            log.info("mcp_server_switched", server=name, on=body.on)
            return await listing()

    @router.delete("/api/mcp/servers/{name}")
    async def remove_server(name: str) -> McpServersView:
        """Remove a server added on the Connectors page, or let go of a try: its tools leave the runs that start after,
        and Dot forgets its address and its sign-in. One added from another agent of this Mac is taken back as
        Customize › Skills › Found on this Mac takes it back, and stopped now (item 497). A server of Dot's config is
        the owner's to take out of ``config.toml``, and one of a plugin goes with its plugin
        (``DELETE /api/installed-plugins/{name}``)."""
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        found: tuple[McpServerConfig, McpOrigin] | None = servers.get(name)
        if found is None:
            raise NotFoundError("Dot has no connected app of this name", {"server": name})
        if found[1] == "elsewhere":
            path = added_servers_path()
            record = await asyncio.to_thread(read_added_servers, path)
            item = next((one for one in record.servers if dot_server_name(one.name) == name), None)
            if item is None:
                raise NotFoundError("no connected app of this name was added from another app", {"server": name})
            await asyncio.to_thread(take_back_added_server, path, item.agent, item.name)
            await hub.disconnect(name)
            refresh_mcp_tools(ctx.runtime.registry, hub)
            log.info("mcp_server_taken_back", server=name, agent=item.agent)
            return await listing()
        if found[1] == "plugin":
            raise RunConflict(
                f"this connected app comes with the plugin {plugin_of(name)}: remove the plugin",
                {"server": name, "set_up": found[1], "plugin": plugin_of(name)},
            )
        if found[1] == "builtin":
            raise RunConflict(
                "this connected app comes with Dot: switch it off, which keeps it from starting",
                {"server": name, "set_up": found[1]},
            )
        if found[1] != "connected":
            raise RunConflict(
                "this connected app is set up in Dot's settings file: take its [[mcp]] block out of config.toml",
                {"server": name, "set_up": found[1]},
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

    @router.delete("/api/installed-plugins/{name}")
    async def remove_plugin(name: str) -> McpServersView:
        """Take the plugin ``name`` out of Dot (item 497): its MCP servers stop now and its skills leave the runs that
        start after, and its folder goes to the Trash, from which it can be put back. A plugin read from a folder of
        ``[plugins] dirs`` is refused: the owner takes that folder out of the config. A plugin whose folder went
        without Dot, of either place, has only its servers left to stop: those the hub runs as it set them up."""
        plugin: Plugin | None = next((one for one in ctx.runtime.plugins.plugins if one.name == name), None)
        running: dict[str, McpServerConfig] = hub.configs()
        # The servers the hub runs as the plugin set them up: one of the same name added since is the owner's.
        still: list[McpServerConfig] = (
            [] if plugin is None else [one for one in plugin.servers if running.get(one.name) == one]
        )
        if plugin is None or (not plugin.root.is_dir() and not still):
            raise NotFoundError("Dot has no plugin of this name", {"plugin": name})
        if not plugin.root.is_dir():
            # Its folder went without Dot: only its servers are left to stop.
            for server in still:
                await hub.disconnect(server.name)
            refresh_mcp_tools(ctx.runtime.registry, hub)
            ctx.runtime.plugins.forget(name)
            await read_again(ctx.runtime.plugins, ctx.runtime.config.plugins, ctx.runtime.config.mcp)
            log.info("plugin_servers_stopped", plugin=name)
            return await listing()
        if not is_dots_own(plugin, plugins_dir()):
            raise RunConflict(
                f"the plugin {name} is read from {plugin.root.parent}, a folder of [plugins] dirs: take that folder "
                "out of the config to remove it",
                {"plugin": name, "folder": str(plugin.root.parent)},
            )
        for server in still:
            await hub.disconnect(server.name)
        refresh_mcp_tools(ctx.runtime.registry, hub)
        moved = await asyncio.to_thread(move_to_trash, plugin.root, trash_folder())
        # The plugins are read again, so that its skills leave the runs that start after and Customize lists it no more;
        # a plugin of its name installed later starts none of the servers stopped here.
        ctx.runtime.plugins.forget(name)
        await read_again(ctx.runtime.plugins, ctx.runtime.config.plugins, ctx.runtime.config.mcp)
        log.info("plugin_removed", plugin=name, trash=str(moved))
        return await listing()

    @router.get(CALLBACK_PATH)
    async def come_back(
        state: str = "",
        code: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
        iss: str | None = None,
    ) -> HTMLResponse:
        """Where the authorization server sends the browser back: the code goes to the sign-in that waits for it, an
        account's of Google or Microsoft (item 422) first, else an MCP server's. The page says how it came out (item
        437); the words the server sent with a no are shown only for a state that waits."""
        came: CameBack | None = await ctx.runtime.accounts.come_back(state, code, error, error_description, time.time())
        if came is not None:
            return _answer(account_callback(came))
        # Read before the code is handed over: a record that cannot be read leaves the sign-in waiting, not answered.
        servers: dict[str, tuple[McpServerConfig, McpOrigin]] = await everything()
        server: str | None = sign_ins.answer(state, code, iss, error)
        if server is None:
            return _answer(Callback(outcome="unknown", subject=None, reason=None))
        found: tuple[McpServerConfig, McpOrigin] | None = servers.get(server)
        subject: Subject = mcp_subject(server, None if found is None else found[0])
        if error is not None or code is None:
            return _answer(
                Callback(outcome="declined", subject=subject, reason=denial_reason(error, error_description))
            )
        return _answer(Callback(outcome="back", subject=subject, reason=None))

    return router
