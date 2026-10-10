"""The pace of public and social actions on a site, so that an account Dot works on keeps to a rhythm its platform
tolerates.

A browser action that carries ``public_post`` or ``external_send`` (a post, a reply, a repost, a message) is public; one
that carries ``social`` (a follow, a like, a subscription) is social. Each is counted on the site of the page it happens
on (its host, without ``www.``), across runs and threads, in a sliding hour and day (``[policy.pace]``; 0 removes a
limit), and public ones keep ``public_spacing_s`` apart. A pace is a limit, not a question: no approval mode waives it.
"""

from collections.abc import Sequence
from urllib.parse import SplitResult, urlsplit

from dotagent.config import PaceConfig
from dotagent.core.text import until_text
from dotagent.core.types import FrozenModel
from dotagent.policy.types import ActionInfo, ActionKind, Flag, PaceClass

HOUR_S: float = 3600.0
DAY_S: float = 86400.0
_PUBLIC_FLAGS: frozenset[Flag] = frozenset({"public_post", "external_send"})
# The kinds of browser_act that act on a page; its target is the page's address.
_BROWSER_ACTING_KINDS: frozenset[ActionKind] = frozenset({"browser_click", "browser_input"})


class PacedAction(FrozenModel):
    """An action the pace counts: the site it happens on and its class."""

    host: str
    pace: PaceClass


def pace_class(flags: Sequence[Flag]) -> PaceClass | None:
    """The class of an action with ``flags``: public before social; ``None`` when the pace does not count it."""
    if any(flag in _PUBLIC_FLAGS for flag in flags):
        return "public"
    if "social" in flags:
        return "social"
    return None


def paced_action(info: ActionInfo) -> PacedAction | None:
    """The site and the class of a browser action the pace counts; ``None`` for any other action."""
    pace: PaceClass | None = pace_class(info.flags)
    if info.kind not in _BROWSER_ACTING_KINDS or pace is None:
        return None
    parts: SplitResult = urlsplit(info.target)
    host: str = (parts.hostname or "").removeprefix("www.")
    if parts.scheme not in ("http", "https") or host == "":
        return None
    return PacedAction(host=host, pace=pace)


def _windows(config: PaceConfig, pace: PaceClass) -> tuple[tuple[str, float, int], ...]:
    if pace == "public":
        return (("hour", HOUR_S, config.public_per_hour), ("day", DAY_S, config.public_per_day))
    return (("hour", HOUR_S, config.social_per_hour), ("day", DAY_S, config.social_per_day))


def pace_refusal(config: PaceConfig, action: PacedAction, times: Sequence[float], now: float) -> str | None:
    """Why ``action`` would break the pace of its site, from the times of the actions of its class there in the last
    day; ``None`` when it keeps to it. The text names the next time one is allowed."""
    for name, window_s, limit in _windows(config, action.pace):
        recent: list[float] = sorted(ts for ts in times if ts > now - window_s)
        if limit == 0 or len(recent) < limit:
            continue
        next_at: float = recent[len(recent) - limit] + window_s
        return (
            f"Pace for {action.host}: {len(recent)} of {limit} {action.pace} actions in the last {name}; the next is "
            f"allowed at {until_text(next_at, now)}. Do other work meanwhile, or leave the rest to a later session."
        )
    return None


def spacing_wait(config: PaceConfig, action: PacedAction, times: Sequence[float], now: float) -> float:
    """The seconds a public action waits so that it comes ``public_spacing_s`` after the last public one on its site."""
    if action.pace != "public" or not times:
        return 0.0
    return max(0.0, max(times) + config.public_spacing_s - now)
