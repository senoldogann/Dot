"""Files written whole: a reader, or a process that ends halfway, never meets one half written."""

import os
import tempfile
from pathlib import Path


def write_whole(path: Path, text: str) -> None:
    """Write the file whole or not at all: into a new file ``mkstemp`` makes beside it, readable by this user only,
    then moved over it."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
