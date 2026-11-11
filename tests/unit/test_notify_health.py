"""HTTP answering is not the same as a phone being able to hear Dot."""

from dotagent.notify.health import NotifyHealth


def test_a_dead_push_watch_is_down_while_a_process_with_no_watch_is_off() -> None:
    quiet: NotifyHealth = NotifyHealth()
    assert quiet.summary() == "off"
    quiet.mark_live("push")
    assert quiet.summary() == "ok"
    quiet.mark_live("apns")
    quiet.mark_down("push")
    assert quiet.summary() == "down"
    healed: NotifyHealth = NotifyHealth()
    healed.mark_live("apns")
    assert healed.summary() == "ok"
