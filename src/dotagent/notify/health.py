"""Whether the tasks that reach a phone are alive.

The HTTP server staying up does not mean a phone can hear Dot. ``push`` is the web push watch and ``apns`` the Apple
push watch: ``off`` when that watch was not started, ``live`` while its task runs, ``down`` when the task ended on its
own. The summary is ``down`` when any watch that was started has died, ``ok`` when every started watch is live, and
``off`` when none was started.
"""

from typing import Literal

ChannelState = Literal["off", "live", "down"]
NotifySummary = Literal["ok", "down", "off"]


class NotifyHealth:
    """The phone watches of one daemon. Mutated as those tasks start and end; safe to share with the health route."""

    def __init__(self) -> None:
        self.push: ChannelState = "off"
        self.apns: ChannelState = "off"

    def mark_live(self, channel: Literal["push", "apns"]) -> None:
        if channel == "push":
            self.push = "live"
        else:
            self.apns = "live"

    def mark_down(self, channel: Literal["push", "apns"]) -> None:
        if channel == "push":
            self.push = "down"
        else:
            self.apns = "down"

    def summary(self) -> NotifySummary:
        started: list[ChannelState] = [state for state in (self.push, self.apns) if state != "off"]
        if not started:
            return "off"
        if any(state == "down" for state in started):
            return "down"
        return "ok"
