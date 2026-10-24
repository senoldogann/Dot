"""What the tests of the pictures an answer names (item 617) share: the first bytes of each kind of picture, and a home
folder laid out as the owner's Mac is (Dot's workspace deep in ``~/Library/Application Support/Dot``, spaces in the path
and all), made inside a folder of the test's own. Nothing here touches the owner's home."""

from dataclasses import dataclass
from pathlib import Path

from dotagent.channels.pictures import Pictures

# The first bytes of each kind of picture, and a file that none of them starts.
HEADS: dict[str, bytes] = {
    "png": b"\x89PNG\r\n\x1a\n" + bytes(24),
    "jpeg": b"\xff\xd8\xff\xe0" + bytes(28),
    "gif": b"GIF89a" + bytes(26),
    "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 " + bytes(16),
    "heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1" + bytes(8),
}
NOT_IMAGE: bytes = b"just some words in a file named like a picture\n"


@dataclass
class Layout:
    """A home folder laid out as the owner's, Dot's workspace inside its Library, the folder pictures are copied into
    to be sent, and the finder of the pictures an answer names over them."""

    root: Path
    home: Path
    workspace: Path
    staging: Path
    finder: Pictures

    def make(self, path: Path, kind: str = "png", data: bytes | None = None) -> Path:
        """A file at ``path`` that starts as a picture of ``kind`` (or holds ``data``)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(HEADS[kind] if data is None else data)
        return path


def layout(root: Path) -> Layout:
    """The layout inside the folder ``root``, which a test makes (its ``tmp_path``)."""
    home: Path = root / "Users" / "owner"
    workspace: Path = home / "Library" / "Application Support" / "Dot" / "workspace"
    workspace.mkdir(parents=True)
    return Layout(root, home, workspace, home / "Pictures" / "Dot", Pictures(workspace, home))
