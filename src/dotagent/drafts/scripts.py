"""The AppleScripts that hand a draft to Mail or to Messages. The values of a draft reach them as the arguments of the
``run`` handler, never as words of the script, so that no text of a draft can change what the script does."""

from dotagent.store.models import DraftChannel, DraftRecord

# Arguments: the subject, the body, how many "to" recipients follow, those, then the "cc" ones.
_MAIL: str = "\n".join(
    (
        "on run argv",
        "  set theSubject to item 1 of argv",
        "  set theBody to item 2 of argv",
        "  set toCount to (item 3 of argv) as integer",
        '  tell application "Mail"',
        "    set theMessage to make new outgoing message with properties "
        "{subject:theSubject, content:theBody, visible:false}",
        "    tell theMessage",
        "      repeat with i from 1 to toCount",
        "        make new to recipient at end of to recipients with properties {address:(item (3 + i) of argv)}",
        "      end repeat",
        "      repeat with i from (4 + toCount) to (count of argv)",
        "        make new cc recipient at end of cc recipients with properties {address:(item i of argv)}",
        "      end repeat",
        "    end tell",
        "    send theMessage",
        "  end tell",
        "end run",
    )
)
# Arguments: the body, then the recipients, each sent the body over iMessage.
_MESSAGE: str = "\n".join(
    (
        "on run argv",
        "  set theBody to item 1 of argv",
        '  tell application "Messages"',
        "    set theService to 1st account whose service type = iMessage",
        "    repeat with i from 2 to (count of argv)",
        "      send theBody to participant (item i of argv) of theService",
        "    end repeat",
        "  end tell",
        "end run",
    )
)
SCRIPTS: dict[DraftChannel, str] = {"mail": _MAIL, "message": _MESSAGE}


def send_arguments(draft: DraftRecord) -> tuple[str, ...]:
    """The arguments the script of the draft's channel takes (see the scripts above)."""
    if draft.channel == "mail":
        return (draft.subject, draft.body, str(len(draft.to)), *draft.to, *draft.cc)
    return (draft.body, *draft.to)
