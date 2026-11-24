"""The web app's outbox waits out the daemon's one 409 that passes with time, a message being sent already (item
1523), and it knows it by the daemon's own words: a daemon that changed them would make the outbox refuse every message
whose reply was lost, and a page that changed its copy of them would wait a day for a 409 that never passes."""

from pathlib import Path

_ROOT: Path = Path(__file__).resolve().parents[2]
_WORDS: str = "the message is being sent already"


def test_the_outbox_and_the_daemon_say_the_same_words_for_a_message_that_is_being_sent() -> None:
    daemon: str = (_ROOT / "src" / "dotagent" / "server" / "sending.py").read_text(encoding="utf-8")
    page: str = (_ROOT / "src" / "dotagent" / "server" / "static" / "js" / "outboxrules.js").read_text(encoding="utf-8")
    assert f'"{_WORDS}"' in daemon
    assert f'"{_WORDS}"' in page
