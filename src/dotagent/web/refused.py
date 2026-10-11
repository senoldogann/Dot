"""The hosts that turned a run's plain requests away (ROADMAP item 98): the next fetch of such a host goes to the
browser first instead of meeting the same wall again. One per run, held by the run's tool context."""

from urllib.parse import urlsplit


def host_of(url: str) -> str:
    """The host of ``url`` in lower case, the port kept (two ports of one machine are two sites)."""
    return urlsplit(url).netloc.lower()


class RefusedHosts:
    """The hosts of a run that refused a plain request; ``note`` adds the host of an address, ``remembers`` asks."""

    def __init__(self) -> None:
        self._hosts: set[str] = set()

    def note(self, url: str) -> None:
        self._hosts.add(host_of(url))

    def remembers(self, url: str) -> bool:
        return host_of(url) in self._hosts

    def __len__(self) -> int:
        return len(self._hosts)
