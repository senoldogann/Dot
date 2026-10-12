"""The ASGI application: the JSON API, the live streams and the browser UI, behind the local guard."""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import JsonValue, TypeAdapter

from dotagent import __version__
from dotagent.core.errors import (
    BrowserError,
    ConfigError,
    DotError,
    InvalidValueError,
    NotFoundError,
    ProviderError,
    RunConflict,
    ToolExecutionError,
    ToolInputError,
    UndoError,
)
from dotagent.core.logs import get_logger
from dotagent.server.context import ServerContext
from dotagent.server.guard import LocalGuard
from dotagent.server.routes_agents import agents_router
from dotagent.server.routes_browser import browser_router
from dotagent.server.routes_campaigns import campaigns_router
from dotagent.server.routes_data import memories_router, rules_router, schedules_router
from dotagent.server.routes_keys import keys_router
from dotagent.server.routes_runs import runs_router
from dotagent.server.routes_system import health_router, system_router
from dotagent.server.routes_themes import themes_router
from dotagent.server.routes_threads import threads_router
from dotagent.server.views import ErrorView

log = get_logger(__name__)

STATIC_DIR: Path = Path(__file__).parent / "static"
_JSON_LIST: TypeAdapter[list[JsonValue]] = TypeAdapter(list[JsonValue])
_SERVER_FAULT: int = 500

# Errors the caller can fix or must wait out map to 4xx (a change that cannot be taken back, as the files stand now, is
# a conflict), a failing model service to 502; every other error is a fault of Dot itself: it is answered with 500 and
# logged.
_STATUS_BY_ERROR: tuple[tuple[type[DotError], int], ...] = (
    (NotFoundError, 404),
    (RunConflict, 409),
    (UndoError, 409),
    (InvalidValueError, 422),
    (ConfigError, 422),
    (ToolInputError, 422),
    (ToolExecutionError, 422),
    (BrowserError, 422),
    (ProviderError, 502),
)


def status_for(error: DotError) -> int:
    for kind, status in _STATUS_BY_ERROR:
        if isinstance(error, kind):
            return status
    return _SERVER_FAULT


def _error_response(status: int, message: str, context: dict[str, JsonValue]) -> JSONResponse:
    return JSONResponse(ErrorView(message=message, context=context).model_dump(mode="json"), status_code=status)


def create_app(ctx: ServerContext) -> FastAPI:
    """Build the application for one running Dot; the guard wraps everything, the UI files answer last."""
    app: FastAPI = FastAPI(title="Dot", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(health_router())
    app.include_router(system_router(ctx))
    app.include_router(runs_router(ctx))
    app.include_router(threads_router(ctx))
    app.include_router(rules_router(ctx))
    app.include_router(memories_router(ctx))
    app.include_router(schedules_router(ctx))
    app.include_router(campaigns_router(ctx))
    app.include_router(agents_router(ctx))
    app.include_router(browser_router(ctx))
    app.include_router(keys_router(ctx))
    app.include_router(themes_router())

    @app.exception_handler(DotError)
    async def dot_error(request: Request, exc: DotError) -> JSONResponse:
        status: int = status_for(exc)
        if status == _SERVER_FAULT:
            log.error(
                "request_failed", method=request.method, path=request.url.path, error=exc.message, context=exc.context
            )
        return _error_response(status, exc.message, exc.context)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """What is wrong with each field, without the value that was refused: it may be a secret, an API key for one."""
        problems: list[JsonValue] = _JSON_LIST.validate_python(
            jsonable_encoder([{key: value for key, value in error.items() if key != "input"} for error in exc.errors()])
        )
        return _error_response(422, "the request is not valid", {"errors": problems})

    app.add_middleware(LocalGuard, token=ctx.token, port=ctx.port, reach=frozenset(ctx.runtime.config.server.reach))
    # The web app manifest has a type of its own that the platform's table may not know.
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
