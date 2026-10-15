"""The MCP servers the owner added on Connect apps (item 279), kept in Dot's home (:func:`connected_servers_path`): each
as Dot connects to it, by its address or its command, and whether it signs in. Dot connects them at its start after the
servers of its config, its plugins and the other agents, and while it runs as soon as one is added; a name one of
those has leaves this one out."""

import json
import threading
from pathlib import Path

from pydantic import ValidationError

from dotagent.config import McpServerConfig
from dotagent.core.errors import McpError
from dotagent.core.files import write_whole
from dotagent.core.types import FrozenModel

# One change of the record at a time in this process: each reads it, changes it and replaces it.
_RECORD_LOCK: threading.Lock = threading.Lock()


class ConnectedServers(FrozenModel):
    servers: tuple[McpServerConfig, ...]


def read_connected(path: Path) -> ConnectedServers:
    """The record at ``path``; an empty one when there is none. Raises McpError when it cannot be read."""
    if not path.exists():
        return ConnectedServers(servers=())
    try:
        return ConnectedServers.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise McpError(
            f"Dot's record of the MCP servers added on Connect apps cannot be read ({type(error).__name__})",
            {"path": str(path)},
        ) from error


def _write(path: Path, record: ConnectedServers) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_whole(path, json.dumps(record.model_dump(mode="json"), indent=2) + "\n")


def keep_connected(path: Path, config: McpServerConfig) -> None:
    """Keep ``config`` in the record, in the place of a server of the same name."""
    with _RECORD_LOCK:
        record: ConnectedServers = read_connected(path)
        kept: tuple[McpServerConfig, ...] = tuple(server for server in record.servers if server.name != config.name)
        _write(path, ConnectedServers(servers=(*kept, config)))


def forget_connected(path: Path, name: str) -> bool:
    """Take the server ``name`` out of the record; whether it was there."""
    with _RECORD_LOCK:
        record: ConnectedServers = read_connected(path)
        kept: tuple[McpServerConfig, ...] = tuple(server for server in record.servers if server.name != name)
        if len(kept) == len(record.servers):
            return False
        _write(path, ConnectedServers(servers=kept))
        return True
