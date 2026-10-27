"""The ASGI application: the JSON API, the live streams and the browser UI, behind the local guard."""

import asyncio
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import JsonValue, TypeAdapter
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dotagent import __version__
from dotagent.core.errors import (
    BrowserError,
    ChannelError,
    ConfigError,
    DotError,
    ElsewhereError,
    GalleryError,
    InvalidValueError,
    NotFoundError,
    PairingRefused,
    PhoneRefused,
    PhoneSetupError,
    PreviewError,
    ProviderError,
    RunConflict,
    SignInError,
    ToolExecutionError,
    ToolInputError,
    UndoError,
    VaultRefused,
    VoiceError,
)
from dotagent.core.logs import get_logger
from dotagent.core.paths import looks_dir, phones_path
from dotagent.core.text import redact_json, redact_secrets, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.mcp.masking import masked_context
from dotagent.previews.quicklook import QuickLook, open_quick_look
from dotagent.server.compress import GzipExceptEventStreams
from dotagent.server.context import ServerContext
from dotagent.server.guard import LocalGuard
from dotagent.server.kept_pages import KeptPages, ServedPages, keep_pages
from dotagent.server.phones import PairedPhones
from dotagent.server.routes_about import about_router
from dotagent.server.routes_accounts import accounts_router
from dotagent.server.routes_agents import agents_router
from dotagent.server.routes_assistant import assistant_router
from dotagent.server.routes_browser import browser_router
from dotagent.server.routes_campaigns import campaigns_router
from dotagent.server.routes_channels import channels_router
from dotagent.server.routes_connections import connections_router
from dotagent.server.routes_data import memories_router, rules_router, schedules_router
from dotagent.server.routes_drafts import drafts_router
from dotagent.server.routes_files import files_router, looks_router
from dotagent.server.routes_gallery import gallery_router
from dotagent.server.routes_harness import harness_router
from dotagent.server.routes_keys import keys_router
from dotagent.server.routes_mcp import mcp_router
from dotagent.server.routes_pages import pages_router
from dotagent.server.routes_phone import phone_router
from dotagent.server.routes_proactive import proactive_router
from dotagent.server.routes_queue import queue_router
from dotagent.server.routes_reactions import reactions_router
from dotagent.server.routes_runs import runs_router
from dotagent.server.routes_snapshots import snapshots_router
from dotagent.server.routes_system import health_router, proof_router, system_router
from dotagent.server.routes_teach import teach_router
from dotagent.server.routes_teams import teams_router
from dotagent.server.routes_themes import themes_router
from dotagent.server.routes_threads import threads_router
from dotagent.server.routes_trading import trading_router
from dotagent.server.routes_vault import vault_router
from dotagent.server.routes_vaultitems import vault_items_router
from dotagent.server.routes_voice import voice_router
from dotagent.server.routes_websearch import websearch_router
from dotagent.server.views import ErrorView

log = get_logger(__name__)

STATIC_DIR: Path = Path(__file__).parent / "static"
_JSON_LIST: TypeAdapter[list[JsonValue]] = TypeAdapter(list[JsonValue])
_SERVER_FAULT: int = 500
# The words of an error that is not one of Dot's own, as a response carries them; the log keeps them whole.
_FAULT_CHARS: int = 500

# Errors the caller can fix or must wait out map to 4xx (a change that cannot be taken back, as the files stand now, is
# a conflict), a failing model service to 502; every other error is a fault of Dot itself: it is answered with 500 and
# logged.
_STATUS_BY_ERROR: tuple[tuple[type[DotError], int], ...] = (
    (PairingRefused, 403),
    (PhoneRefused, 403),
    (VaultRefused, 403),
    (NotFoundError, 404),
    (RunConflict, 409),
    (UndoError, 409),
    (ElsewhereError, 409),
    (GalleryError, 409),
    (InvalidValueError, 422),
    (ConfigError, 422),
    (ToolInputError, 422),
    (ToolExecutionError, 422),
    (BrowserError, 422),
    (PreviewError, 422),
    (PhoneSetupError, 422),
    (ChannelError, 422),
    (SignInError, 422),
    (VoiceError, 422),
    (ProviderError, 502),
)


def status_for(error: DotError) -> int:
    for kind, status in _STATUS_BY_ERROR:
        if isinstance(error, kind):
            return status
    return _SERVER_FAULT


def _error_response(status: int, message: str, context: dict[str, JsonValue]) -> JSONResponse:
    return JSONResponse(ErrorView(message=message, context=context).model_dump(mode="json"), status_code=status)


def _masked_error_context(context: JsonObject) -> JsonObject:
    """``context`` of a :class:`DotError` with the secrets in it masked: an address's key and an argument's
    (:func:`dotagent.mcp.masking.masked_context`), the secrets Dot holds, the keys of a known form and the value of a
    field that names a secret. Blocking."""
    masked: JsonValue = redact_json(masked_context(context))
    return masked if isinstance(masked, dict) else {}


def _fault_response(exc: Exception) -> JSONResponse:
    """The answer to an error that is not one of Dot's own: in the shape of every other error, with its kind and its
    words, so a page can say what failed."""
    words: str = truncate_middle(redact_secrets(str(exc)), _FAULT_CHARS)
    return _error_response(
        _SERVER_FAULT, "Dot failed on an error it did not expect", {"error": f"{type(exc).__name__}: {words}"}
    )


class _AnswerFaults:
    """Answers an error that is not one of Dot's own from inside the guard (item 558). Starlette moves the handler of
    ``Exception`` out to the layer outside every middleware, the guard's included, so that its answer went out without
    the security headers and ``Cache-Control: no-store`` of every other answer of the API, and was raised again for
    uvicorn to log a second time, whole and unmasked. Here the guard's own ``send`` carries the answer, nothing is
    raised again, and the log has the one ``request_crashed`` entry. An answer already begun (a stream) cannot be
    taken back: that error goes on."""

    def __init__(self, app: ASGIApp) -> None:
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started: bool = False

        async def watching(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, watching)
        except Exception as exc:
            if started:
                raise
            log.error("request_crashed", method=scope["method"], path=scope["path"], exc_info=exc)
            await _fault_response(exc)(scope, receive, send)


def create_app(ctx: ServerContext) -> FastAPI:
    """Build the application for one running Dot; the guard wraps everything, the UI files answer last, as they were
    when it was built (item 309, :mod:`dotagent.server.kept_pages`)."""
    pages: KeptPages = keep_pages(STATIC_DIR)
    app: FastAPI = FastAPI(title="Dot", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(health_router())
    phones: PairedPhones = ctx.phones if ctx.phones is not None else PairedPhones.load(phones_path())
    app.include_router(proof_router(ctx.token, phones))
    app.include_router(system_router(ctx, pages.build))
    app.include_router(about_router(ctx))
    app.include_router(runs_router(ctx))
    app.include_router(queue_router(ctx))
    app.include_router(reactions_router(ctx))
    app.include_router(trading_router(ctx))
    app.include_router(threads_router(ctx))
    app.include_router(rules_router(ctx))
    app.include_router(memories_router(ctx))
    app.include_router(drafts_router(ctx))
    app.include_router(schedules_router(ctx))
    app.include_router(proactive_router(ctx))
    app.include_router(campaigns_router(ctx))
    app.include_router(harness_router(ctx))
    app.include_router(agents_router(ctx))
    app.include_router(teams_router(ctx))
    app.include_router(teach_router(ctx))
    app.include_router(browser_router(ctx))
    app.include_router(connections_router(ctx))
    app.include_router(mcp_router(ctx))
    app.include_router(gallery_router(ctx))
    app.include_router(keys_router(ctx))
    app.include_router(websearch_router(ctx))
    app.include_router(themes_router())
    app.include_router(pages_router(ctx))
    looks: QuickLook = open_quick_look(looks_dir())
    app.include_router(files_router(ctx, looks))
    app.include_router(looks_router(looks))
    app.include_router(phone_router(ctx, phones))
    app.include_router(channels_router(ctx))
    app.include_router(snapshots_router(ctx))
    app.include_router(assistant_router(ctx))
    app.include_router(accounts_router(ctx))
    app.include_router(vault_router(ctx))
    app.include_router(vault_items_router(ctx))
    app.include_router(voice_router())

    @app.exception_handler(DotError)
    async def dot_error(request: Request, exc: DotError) -> JSONResponse:
        status: int = status_for(exc)
        # An error's context may hold an address with a key in it or an argument that is one (item 690): it is masked
        # here, for the response and the log alike, so that no route has to. The secrets Dot holds are read from the
        # env file, off the event loop.
        masked: JsonObject = await asyncio.to_thread(_masked_error_context, exc.context)
        if status == _SERVER_FAULT:
            log.error("request_failed", method=request.method, path=request.url.path, error=exc.message, context=masked)
        return _error_response(status, exc.message, masked)

    @app.exception_handler(HTTPException)
    async def framework_error(request: Request, exc: HTTPException) -> JSONResponse:
        """An answer of the web framework itself (no such path, a method a path does not take) in the shape of every
        other error of the API, with its headers."""
        return JSONResponse(
            ErrorView(message=str(exc.detail), context={"method": request.method, "path": request.url.path}).model_dump(
                mode="json"
            ),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """What is wrong with each field, without the value that was refused: it may be a secret, an API key for one."""
        problems: list[JsonValue] = _JSON_LIST.validate_python(
            jsonable_encoder([{key: value for key, value in error.items() if key != "input"} for error in exc.errors()])
        )
        return _error_response(422, "the request is not valid", {"errors": problems})

    # Added before the guard, so that the guard wraps it and its answer carries the guard's headers (item 558).
    app.add_middleware(_AnswerFaults)
    app.add_middleware(
        LocalGuard,
        token=ctx.token,
        port=ctx.port,
        reach=frozenset(ctx.runtime.config.server.reach),
        phones=phones,
    )
    # Added last to be the outermost layer, so that the pages' answers go through gzip as much as the API's (item 381).
    app.add_middleware(GzipExceptEventStreams)
    # The web app manifest has a type of its own that the platform's table may not know.
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    app.mount("/", ServedPages(pages), name="ui")
    return app
