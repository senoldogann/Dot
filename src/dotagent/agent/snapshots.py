"""A capture of Snapshots kept as attachments (item 405): what :mod:`dotagent.desktop.snapshots` takes of the window
in front, stored in the workspace as the files of a message are, so that the composer lists them and a run reads them.
Apart from the capture so that the desktop module needs nothing of the agent's."""

import time
from collections.abc import Callable
from pathlib import Path

from dotagent.agent.attachments import StoredAttachment, attachment_summary, store_attachment
from dotagent.config import DesktopConfig
from dotagent.desktop.snapshots import Capture, Kept, SnapshotTaken, capture_front_window


def stored_taker(desktop: DesktopConfig, workspace: Path) -> Callable[[bool], Kept]:
    """What the service captures with: the window in front, kept in ``workspace`` as a JPEG and, with its text, a
    Markdown file beside it, with where the window was and the picture's file for the flight (item 414). Blocking: the
    service calls it off the event loop."""

    def take(with_text: bool) -> Kept:
        taken: Capture = capture_front_window(desktop, with_text)
        picture: StoredAttachment = store_attachment(workspace, f"{taken.stem}.jpg", "image/jpeg", taken.image)
        files: list[StoredAttachment] = [picture]
        if taken.text:
            files.append(store_attachment(workspace, f"{taken.stem}.md", "text/markdown", taken.text.encode("utf-8")))
        snapshot: SnapshotTaken = SnapshotTaken(
            app=taken.app, title=taken.title, files=attachment_summary(files), taken_at=time.time()
        )
        return Kept(snapshot=snapshot, frame=taken.frame, picture=picture.path)

    return take
