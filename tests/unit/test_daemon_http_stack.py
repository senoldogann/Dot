"""How the daemon serves (item 583's review): its HTTP parser and its event loop are named, not left to uvicorn's
``auto``. Dot's dependencies bring ``uvicorn[standard]``'s extras, httptools and uvloop among them, so ``auto`` would
have the daemon that holds the owner's API token serve with those instead of h11 and asyncio, which it always did."""

import httptools  # noqa: F401  # installed with uvicorn's extras: the premise of the test
import uvicorn
from fastapi import FastAPI
from uvicorn.protocols.http.h11_impl import H11Protocol

from dotagent.server.daemon import serving_config


def test_the_daemon_serves_with_h11_and_asyncio_whatever_uvicorn_would_choose() -> None:
    config: uvicorn.Config = serving_config(FastAPI())
    assert (config.http, config.loop) == ("h11", "asyncio")
    config.load()
    assert config.http_protocol_class is H11Protocol
    assert config.get_loop_factory().__module__.startswith("asyncio")
    # What `auto` makes of the same Python environment: the parser and the loop the daemon does not use.
    auto: uvicorn.Config = uvicorn.Config(FastAPI(), lifespan="off", ws="none")
    auto.load()
    assert auto.http_protocol_class is not H11Protocol
    assert auto.get_loop_factory().__module__ == "uvloop"
