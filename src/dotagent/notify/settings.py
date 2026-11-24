"""What each phone wants to hear (item 1421): three switches, one for each kind of push (:data:`PUSH_GROUPS`), kept for
the phone whose key set them and read by the watch that sends the app its alerts. All three are on until the phone says
otherwise. They are Dot's own record, not a setting of the iPhone's: iOS's switch for Dot's notifications stands above
them."""

from pydantic import StrictBool, ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.notify.push import PushGroup
from dotagent.store.db import Store

log = get_logger(__name__)

# The kv row of a phone's switches, by its id.
_KEY_PREFIX: str = "phone.push."


class PushSettings(FrozenModel):
    """The three kinds of push a phone is told: ``needs_you`` (something waits for the owner's answer or look: an
    approval, a question, a secret, a hand-off, and Dot's own failures), ``ready`` (something is ready for them to look
    over: a draft, what Dot tells them) and ``done`` (the end of a run, of a schedule's run or of a campaign)."""

    needs_you: StrictBool
    ready: StrictBool
    done: StrictBool

    def wants(self, group: PushGroup) -> bool:
        """Whether the phone is told a push of ``group``."""
        return {"needs_you": self.needs_you, "ready": self.ready, "done": self.done}[group]


# What a phone that never said hears: all of it.
DEFAULT_PUSH_SETTINGS: PushSettings = PushSettings(needs_you=True, ready=True, done=True)


def _key(phone_id: str) -> str:
    return f"{_KEY_PREFIX}{phone_id}"


async def push_settings_of(store: Store, phone_id: str) -> PushSettings:
    """The switches of ``phone_id``; all on for a phone that set none. A row that cannot be read is logged and read as
    all on: a phone hears more rather than miss what waits for its owner."""
    kept: str | None = await store.kv_get(_key(phone_id))
    if kept is None:
        return DEFAULT_PUSH_SETTINGS
    try:
        return PushSettings.model_validate_json(kept)
    except ValidationError as error:
        log.warning("phone_push_settings_unreadable", phone=phone_id, error=str(error))
        return DEFAULT_PUSH_SETTINGS


async def keep_push_settings(store: Store, phone_id: str, settings: PushSettings) -> None:
    """Keep the switches of ``phone_id``."""
    await store.kv_set(_key(phone_id), settings.model_dump_json())


async def forget_push_settings(store: Store, phone_id: str) -> None:
    """Forget the switches of ``phone_id``: the phone was removed."""
    await store.kv_delete(_key(phone_id))
