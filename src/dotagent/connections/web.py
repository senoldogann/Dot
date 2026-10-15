"""Whether the owner is signed in to the web's applications in Dot's own browser (item 278), read from the cookies the
browser holds: by host and name only, never by value. A browser that runs is asked (its cookies are written to its
profile some seconds after they change); one that does not is read from a copy of its profile's cookie file."""

import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from pathlib import Path

from dotagent.connections.catalogue import WebApp
from dotagent.core.errors import BrowserError

# A cookie as the host that holds it and its name.
type CookieKey = tuple[str, str]

# Where Chrome keeps a profile's cookies: in the profile's network folder since Chrome 96, and at its top before.
_COOKIE_FILES: tuple[str, ...] = ("Default/Network/Cookies", "Default/Cookies")
# The files SQLite keeps beside a database while it is written, copied with it so the copy holds what Chrome wrote last.
_SIDECARS: tuple[str, ...] = ("-wal", "-journal")
# Chrome counts the time of a cookie in microseconds since 1601-01-01; this many seconds lie between that and 1970.
_CHROME_EPOCH_S: int = 11_644_473_600
_MICROSECONDS: int = 1_000_000


def chrome_time(seconds: float) -> int:
    """The time ``seconds`` (since 1970) as Chrome counts it in its cookie file."""
    return int((seconds + _CHROME_EPOCH_S) * _MICROSECONDS)


def profile_cookies(profile: Path, now: float) -> frozenset[CookieKey]:
    """The cookies Chrome's profile ``profile`` keeps that have not expired at ``now`` (seconds since 1970), read from
    a copy of its cookie file, which Chrome holds open while it runs; none for a profile without one."""
    found: Path | None = next((profile / name for name in _COOKIE_FILES if (profile / name).is_file()), None)
    if found is None:
        return frozenset()
    with tempfile.TemporaryDirectory(prefix="dot-cookies-") as scratch:
        copy: Path = Path(scratch) / found.name
        shutil.copyfile(found, copy)
        for suffix in _SIDECARS:
            sidecar: Path = found.with_name(f"{found.name}{suffix}")
            if sidecar.is_file():
                shutil.copyfile(sidecar, copy.with_name(f"{copy.name}{suffix}"))
        connection: sqlite3.Connection = sqlite3.connect(copy)
        try:
            rows: list[tuple[object, object]] = connection.execute(
                "SELECT host_key, name FROM cookies WHERE has_expires = 0 OR expires_utc > ?", (chrome_time(now),)
            ).fetchall()
        except sqlite3.DatabaseError as error:
            raise BrowserError(
                "the cookies of Dot's browser could not be read", {"file": str(found), "error": str(error)}
            ) from error
        finally:
            connection.close()
    return frozenset((str(host), str(name)) for host, name in rows)


def _under(host: str, domains: Iterable[str]) -> bool:
    bare: str = host.lstrip(".")
    return any(bare == domain or bare.endswith(f".{domain}") for domain in domains)


def signed_in(app: WebApp, cookies: frozenset[CookieKey]) -> bool:
    """Whether ``cookies`` hold one that ``app`` sets only on a signed-in browser, for its domains or a host under one."""
    return any(name in app.cookies and _under(host, app.domains) for host, name in cookies)
