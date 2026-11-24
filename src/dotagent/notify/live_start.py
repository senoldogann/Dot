"""The token that starts a phone's Live Activity by push (item 1425): ActivityKit's push-to-start token, which the app
sends with ``POST /api/phone/live-start-token`` whenever iOS 17.2 or later gives it a new one. It is kept for the phone
whose key sent it, apart from the phone's device token, so that neither registration waits for the other or erases it,
and is read by the watch that sends the app its pushes. Like the other tokens it is never logged."""

from dotagent.store.db import Store

# The kv row of a phone's token, by its id.
_KEY_PREFIX: str = "phone.live-start."


def _key(phone_id: str) -> str:
    return f"{_KEY_PREFIX}{phone_id}"


async def live_start_token_of(store: Store, phone_id: str) -> str | None:
    """The push-to-start token of ``phone_id``, or ``None`` when its app sent none."""
    return await store.kv_get(_key(phone_id))


async def keep_live_start_token(store: Store, phone_id: str, token: str) -> None:
    """Keep ``token`` as the push-to-start token of ``phone_id``, in place of the one before."""
    await store.kv_set(_key(phone_id), token)


async def forget_live_start_token(store: Store, phone_id: str) -> None:
    """Forget the push-to-start token of ``phone_id``: the phone was removed, the app said it has none, or APNs said it
    is of no use any more."""
    await store.kv_delete(_key(phone_id))
