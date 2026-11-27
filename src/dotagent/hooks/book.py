"""The file the hooks are kept in, ``<home>/hooks.json`` (:func:`dotagent.core.paths.hooks_path`): readable by the owner
only, written whole, and changed under a lock of its own (``hooks.json.lock``), so the daemon taking a call and
``dotx hooks`` changing a hook never write over each other. Blocking: the daemon calls these in a thread.

The secrets are kept as they are, since a signature is checked with the secret itself; the file sits in Dot's home,
which the sandbox seals from a run's processes as it seals the API token (``policy/sandbox.py``)."""

import fcntl
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from dotagent.core.errors import ConfigError, NotFoundError
from dotagent.core.files import write_whole
from dotagent.hooks.model import Hook, HookFile

_PRIVATE_FILE_MODE: int = 0o600
_OTHERS_BITS: int = 0o077
_ID_BYTES: int = 6
_SECRET_BYTES: int = 32
_EMPTY: HookFile = HookFile(hooks=())


def new_hook_id(taken: frozenset[str]) -> str:
    """A hook id (12 hex characters) none of ``taken`` is."""
    while True:
        made: str = secrets.token_hex(_ID_BYTES)
        if made not in taken:
            return made


def new_secret() -> str:
    """A hook's secret: 32 random bytes, URL-safe."""
    return secrets.token_urlsafe(_SECRET_BYTES)


def read_hooks(path: Path) -> HookFile:
    """The hooks ``path`` keeps; none when it does not exist. A file others can read, or one that is not a list of
    hooks, is refused rather than taken as none, so that a fault never passes for a hook's removal."""
    try:
        status: os.stat_result = path.stat()
    except FileNotFoundError:
        return _EMPTY
    if status.st_uid != os.getuid() or status.st_mode & _OTHERS_BITS:
        raise ConfigError(
            "the hooks file must belong to you and be readable by nobody else",
            {"path": str(path), "fix": f"chmod 600 {path}"},
        )
    try:
        return HookFile.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise ConfigError(
            f"{path} is not a list of hooks: fix it or delete it, which deletes every hook",
            {"path": str(path), "error": str(error)},
        ) from error


def write_hooks(path: Path, book: HookFile) -> None:
    write_whole(path, book.model_dump_json(indent=2) + "\n")
    os.chmod(path, _PRIVATE_FILE_MODE)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold the lock of ``path``'s hooks file, waiting for another process that holds it."""
    lock: Path = path.with_name(f"{path.name}.lock")
    descriptor: int = os.open(lock, os.O_RDWR | os.O_CREAT, _PRIVATE_FILE_MODE)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def change_hooks[T](path: Path, change: Callable[[HookFile], tuple[HookFile, T]]) -> T:
    """Read the hooks under the lock, write what ``change`` makes of them when it differs, and return what ``change``
    said."""
    with _locked(path):
        book: HookFile = read_hooks(path)
        changed, said = change(book)
        if changed != book:
            write_hooks(path, changed)
        return said


def hook_in(book: HookFile, hook_id: str) -> Hook:
    """The hook ``hook_id`` of ``book``; :class:`NotFoundError` when it has none."""
    for hook in book.hooks:
        if hook.id == hook_id:
            return hook
    raise NotFoundError("no such hook", {"hook_id": hook_id})


def found_hook(book: HookFile, hook_id: str) -> Hook | None:
    """The hook ``hook_id`` of ``book``, or ``None``."""
    return next((hook for hook in book.hooks if hook.id == hook_id), None)


def replaced(book: HookFile, hook: Hook) -> HookFile:
    """``book`` with ``hook`` in the place of the hook of its id."""
    return HookFile(hooks=tuple(hook if kept.id == hook.id else kept for kept in book.hooks))


def without(book: HookFile, hook_id: str) -> HookFile:
    return HookFile(hooks=tuple(kept for kept in book.hooks if kept.id != hook_id))
