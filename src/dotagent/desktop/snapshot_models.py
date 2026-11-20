"""What a Snapshot is once taken and kept (item 405): pure models, so that what hands one on (the server's views, the
agent's tool) loads without the frameworks that take it (:mod:`dotagent.desktop.snapshots`), as on the Linux that runs a
cloud agent's unit tests (item 1270)."""

from pydantic import JsonValue

from dotagent.core.types import FrozenModel
from dotagent.desktop.flight import Frame

# How the shortcut reads on the page.
SHORTCUT_WORDS: str = "⌘ ⌘"


class SnapshotTaken(FrozenModel):
    """A capture kept: the application and the title of the window, the files it was kept as (as the composer lists
    them: ``id``, ``name``, ``kind``, ``size``), and when."""

    app: str
    title: str
    files: list[JsonValue]
    taken_at: float


class Capture(FrozenModel):
    """A capture as taken, before it is kept: the application and the title of the window, the name its files take
    before their extension, the picture as JPEG, the window's text ("" without) and where the window is on the screen
    (for the flash and the flight, item 414)."""

    app: str
    title: str
    stem: str
    image: bytes
    text: str
    frame: Frame


class Kept(FrozenModel):
    """A capture kept: what the composer is handed (``snapshot``), where the window was (``frame``) and the picture's
    file (``picture``), which the card of the flight shows."""

    snapshot: SnapshotTaken
    frame: Frame
    picture: str
