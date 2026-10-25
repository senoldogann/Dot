"""When a secret of Dot's vault may be on the owner's screen (item 621).

The eye of Settings › Vault shows a saved password or card number in Dot's own window. A screenshot of the Mac that a
run takes (``desktop_screenshot``) already leaves out the windows of password managers; for as long as a secret may be
on the screen it leaves out Dot's own windows too. The daemon notes the moment it answers the eye (:func:`note_shown`),
and the page hides the secret again by itself after :data:`SHOWN_FOR_S`, asking the daemon afresh each time the eye
shows it. A number or a password the owner is typing is on the screen as they type it, which this does not see."""

import time

# How long the page shows a secret the eye brought (``SHOWN_FOR_MS`` of ``static/js/vaultforms.js`` says the same), and
# how long after the eye was answered Dot's windows are left out of a screenshot of the Mac: the page's time and a
# margin for what a request and a timer take.
SHOWN_FOR_S: float = 20.0
COVERED_FOR_S: float = 30.0

_until: float = 0.0


def note_shown() -> None:
    """The eye was answered: a secret may be on the owner's screen from now on, for :data:`COVERED_FOR_S`."""
    global _until
    _until = time.monotonic() + COVERED_FOR_S


def on_screen() -> bool:
    """Whether a secret of the vault may be on the owner's screen now."""
    return time.monotonic() < _until
