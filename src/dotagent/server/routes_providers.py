"""Routes for the custom providers (item 1949, docs/models.md): list them, add, change or remove one in
``[providers.custom]`` of the config file and in the running router at once, test one with one small real call when the
owner presses Test, and propose a provider's fields from its documentation page. A custom provider's key goes through
the key routes (``routes_keys``) under the name the daemon gives it, :func:`key_name`; nothing here saves a key, and a
key typed for a test is sent to the provider's address alone and kept nowhere.

Each change is the owner's, on the Mac: a phone's key can list the providers and nothing more."""

import asyncio

import httpx
from fastapi import APIRouter, Request
from pydantic import ValidationError

from dotagent.config import CustomProviderConfig, describe_problems, load_config, model_references
from dotagent.config_edit import set_custom_provider
from dotagent.core.errors import ConfigError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path, env_file_path
from dotagent.doctor import CheckResult
from dotagent.llm.custom import CallCheck, check_call
from dotagent.llm.custom_docs import DocsProposal, propose_with_model
from dotagent.llm.types import custom_provider_name, split_model_ref
from dotagent.secrets import SecretState, optional_secret, remove_env_value, secret_state
from dotagent.server.context import ServerContext
from dotagent.server.guard import refuse_from_a_phone
from dotagent.server.views import (
    CustomProviderRequest,
    CustomProvidersView,
    CustomProviderView,
    CustomTestRequest,
    DocsRequest,
)
from dotagent.web.text import ReadablePage, decode_body, html_to_text
from dotagent.web.types import FetchedDocument

log = get_logger(__name__)

# The most of a documentation page that is read.
_DOCS_BYTES: int = 2_000_000


def key_name(name: str) -> str:
    """The variable the key of the custom provider ``name`` is kept under when Settings adds it: ``DOT_CUSTOM_`` and
    the name in capitals, which no built-in provider, search engine or app of Dot uses, so that a key saved for one
    service never goes to another's address."""
    return f"DOT_CUSTOM_{name.upper().replace('-', '_')}_API_KEY"


def _checked_name(name: str) -> str:
    if not custom_provider_name(name):
        raise ConfigError(
            "a custom provider's name is lower case letters, digits, _ and - from a letter, up to 32, and not the name "
            "of a built-in provider",
            {"name": name},
        )
    return name


def custom_config(
    name: str, request: CustomProviderRequest, before: CustomProviderConfig | None
) -> CustomProviderConfig:
    """The table the request makes of the provider ``name``: its key's name the one it had (a table written by hand
    names its own) or :func:`key_name`, and the times and attempts it had or the defaults."""
    kept_key: str = before.api_key_env if before is not None and before.api_key_env != "" else key_name(name)
    try:
        return CustomProviderConfig.model_validate(
            {
                **({} if before is None else before.model_dump(include={"connect_timeout_s", "read_timeout_s"})),
                **({} if before is None else before.model_dump(include={"attempts", "max_concurrent"})),
                "base_url": request.base_url,
                "protocol": request.protocol,
                "api_key_env": kept_key if request.needs_key else "",
                "headers": request.headers,
                "models": [model.model_dump() for model in request.models],
                "list_models": request.list_models,
            }
        )
    except ValidationError as error:
        raise ConfigError(f"the provider cannot be saved: {describe_problems(error)}", {"name": name}) from error


def origin(url: str) -> str:
    """Where ``url`` sends a request: its scheme, host and port (the scheme's own when it names none)."""
    parsed: httpx.URL = httpx.URL(url)
    port: int = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{parsed.host}:{port}"


def _named_by_another(name: str, others: dict[str, CustomProviderConfig]) -> bool:
    """Whether a provider of ``others`` other than ``name`` names ``name``'s key by hand: then it is not ``name``'s to
    drop."""
    return any(other != name and provider.api_key_env == key_name(name) for other, provider in others.items())


def keeps_its_key(before: CustomProviderConfig | None, after: CustomProviderConfig) -> bool:
    """Whether ``after`` takes the very key ``before`` took, at the same server: the one change after which a key saved
    for the provider may still be used without the owner typing it again."""
    return (
        before is not None
        and before.api_key_env != ""
        and after.api_key_env == before.api_key_env
        and origin(after.base_url) == origin(before.base_url)
    )


def _view(name: str, config: CustomProviderConfig) -> CustomProviderView:
    key_set: bool = config.api_key_env != "" and secret_state(config.api_key_env).source is not None
    return CustomProviderView(name=name, config=config, key_set=key_set)


def references_to(name: str, references: list[tuple[str, str]]) -> list[str]:
    """The settings of ``references`` (each a setting and the model it names) that name a model of ``name``."""
    return [
        f"{setting} = {reference}"
        for setting, reference in references
        if (split_model_ref(reference) or ("", ""))[0] == name
    ]


def page_text(document: FetchedDocument) -> str:
    """A fetched documentation page as text: an HTML page read as its words and links, anything else as it is."""
    body: str = decode_body(document.body, document.charset)
    if "html" not in document.media_type:
        return body
    page: ReadablePage = html_to_text(body, document.url)
    return f"{page.title}\n{page.text}"


def providers_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api/providers/custom")
    # One change of the config file's custom providers at a time.
    writing: asyncio.Lock = asyncio.Lock()

    @router.get("")
    async def custom_providers() -> CustomProvidersView:
        """The custom providers in use, the phone's read-only list among them."""
        providers: dict[str, CustomProviderConfig] = ctx.runtime.router.custom
        return CustomProvidersView(
            providers=[await asyncio.to_thread(_view, name, config) for name, config in sorted(providers.items())]
        )

    @router.put("/{name}")
    async def save_provider(name: str, request: CustomProviderRequest, http: Request) -> CustomProviderView:
        """Add the custom provider ``name`` or change it: the config file keeps it as ``[providers.custom.<name>]``,
        and the next call of one of its models goes to it as it is now. A provider the file cannot hold, or a file
        that cannot be changed, is refused, and nothing changes. A key goes to the server it was saved for alone: a
        change of the address of a provider with a key drops the key Settings saved for it (the owner types it again),
        and is refused for a key from the environment, the Keychain or a name the config file chose, which only the
        owner's own hand can move."""
        refuse_from_a_phone(http.scope, "add or change a provider")
        _checked_name(name)
        async with writing:
            before: CustomProviderConfig | None = ctx.runtime.router.custom.get(name)
            config: CustomProviderConfig = custom_config(name, request, before)
            others: dict[str, CustomProviderConfig] = {
                other: provider for other, provider in ctx.runtime.router.custom.items() if other != name
            }
            if config.api_key_env != "" and any(
                provider.api_key_env == config.api_key_env for provider in others.values()
            ):
                raise ConfigError("another custom provider takes this key", {"name": name, "key": config.api_key_env})
            kept: bool = keeps_its_key(before, config)
            if config.api_key_env != "" and not kept:
                # A key newly sent to this address must be one Settings saves now: a key kept in the environment, the
                # Keychain or under a name the config file chose is the owner's to attach by hand.
                state: SecretState = await asyncio.to_thread(secret_state, config.api_key_env)
                if config.api_key_env != key_name(name) or state.source in ("environment", "keychain"):
                    raise ConfigError(
                        f"the key {config.api_key_env} is not one Settings saved for this address, so it cannot be "
                        "sent there from here: write the table in config.toml, or remove the key from where it is kept",
                        {"name": name, "key": config.api_key_env},
                    )
            await asyncio.to_thread(set_custom_provider, config_path(), name, config)
            # The key Settings saved for the provider stays only while it goes to the server it was saved for: a new
            # address, a table that stops taking a key (and takes one again later) or a new provider of a name once
            # used drop it, before the new address is in service, so the owner types it for the new one.
            dropped: bool = (
                not kept
                and not _named_by_another(name, others)
                and await asyncio.to_thread(remove_env_value, env_file_path(), key_name(name))
            )
            await ctx.runtime.router.set_custom(name, config)
        log.info(
            "custom_provider_saved",
            provider=name,
            protocol=config.protocol,
            models=len(config.models),
            key_dropped=dropped,
        )
        return await asyncio.to_thread(_view, name, config)

    @router.delete("/{name}")
    async def remove_provider(name: str, http: Request) -> CustomProvidersView:
        """Take the custom provider ``name`` out of the config file and out of service, and the key Settings saved for
        it (:func:`key_name`) out of Dot's ``.env``. One a setting of the file still names a model of (a role, a
        standby model, a context size) is refused, as the file would no longer load. A key the file named by hand is
        left where it is."""
        refuse_from_a_phone(http.scope, "remove a provider")
        async with writing:
            if name not in ctx.runtime.router.custom:
                raise NotFoundError("no custom provider has this name", {"name": name})
            named: list[str] = references_to(
                name, model_references(await asyncio.to_thread(load_config, config_path()))
            )
            if named:
                raise ConfigError(
                    f"Dot's settings still name {', '.join(named)}: choose other models for them first",
                    {"name": name, "models": ", ".join(named)},
                )
            await asyncio.to_thread(set_custom_provider, config_path(), name, None)
            await ctx.runtime.router.set_custom(name, None)
            # The key Settings saved under its name goes with it, whatever the table named last; a key the config file
            # named by hand may serve other tools.
            others: dict[str, CustomProviderConfig] = ctx.runtime.router.custom
            dropped: bool = not _named_by_another(name, others) and await asyncio.to_thread(
                remove_env_value, env_file_path(), key_name(name)
            )
        log.info("custom_provider_removed", provider=name, key_removed=dropped)
        return await custom_providers()

    @router.post("/test")
    async def test_provider(request: CustomTestRequest, http: Request) -> CheckResult:
        """One small real call to the provider as the form holds it, only when the owner presses Test: its list of
        models, or a word asked of one model. The key typed in the form is used for this call alone; without one, the
        key saved for the provider is, and only at the address it was saved for."""
        refuse_from_a_phone(http.scope, "test a provider")
        name: str = _checked_name(request.name)
        before: CustomProviderConfig | None = ctx.runtime.router.custom.get(name)
        config: CustomProviderConfig = custom_config(name, request.provider, before)
        listed: set[str] = {model.id for model in config.models}
        if request.model != "" and request.model not in listed and not config.list_models:
            raise ConfigError("the model to test is not among the provider's models", {"model": request.model})
        key: str | None = request.key
        if key is None and config.api_key_env != "":
            if not keeps_its_key(before, config):
                raise ConfigError(
                    "type the provider's key to test it: no key is saved for this address", {"name": name}
                )
            key = await asyncio.to_thread(optional_secret, config.api_key_env)
            if key is None:
                raise ConfigError("type the provider's key to test it: none is saved yet", {"name": name})
        if key is not None and config.api_key_env == "":
            raise ConfigError("the provider takes no key, yet one was given", {"name": name})
        checked: CallCheck = await check_call(name, config, key, request.model)
        log.info("custom_provider_tested", provider=name, ok=checked.ok)
        return CheckResult(name=f"{name} api", ok=checked.ok, required=False, detail=checked.detail)

    @router.post("/docs")
    async def fill_from_docs(request: DocsRequest, http: Request) -> DocsProposal:
        """Propose a provider's fields from its documentation page: Dot reads the page, and asks the fast model once
        only for what the page does not state plainly. Nothing is saved."""
        refuse_from_a_phone(http.scope, "read a provider's documentation")
        document: FetchedDocument = await ctx.runtime.web.get(request.url, _DOCS_BYTES)
        if document.status >= 400:
            raise ConfigError(
                f"the page answered HTTP {document.status}", {"url": request.url, "status": document.status}
            )
        text: str = page_text(document)
        timeout_s: float = ctx.runtime.config.agent.model_call_timeout_s
        return await propose_with_model(ctx.runtime.router, ctx.runtime.roles.fast(), timeout_s, document.url, text)

    return router
