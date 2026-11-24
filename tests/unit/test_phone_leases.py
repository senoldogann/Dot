"""The activity lease of a phone (item 1420): the app says it shows Dot, the lease lives for the time it names (45
seconds by default, 90 at most), ends by itself when the app does not say it again, and ends at once with
``background``. The clock is the test's own."""

from dotagent.notify.leases import DEFAULT_TTL_S, MAX_TTL_S, PhoneLeases, lease_seconds


class _Clock:
    """A clock that stands where a test puts it."""

    def __init__(self, now: float) -> None:
        self.now: float = now

    def __call__(self) -> float:
        return self.now


def test_a_foreground_lease_holds_for_its_ttl_and_is_none_the_moment_it_runs_out() -> None:
    clock = _Clock(1000.0)
    leases = PhoneLeases(clock)
    assert leases.live("p1") is None
    lease = leases.renew("p1", "foreground", None, 45.0)
    assert lease is not None and lease.expires_at == 1045.0 and lease.chat is None
    clock.now = 1044.9
    assert leases.live("p1") == lease
    clock.now = 1045.0
    assert leases.live("p1") is None
    # A lease that ran out is dropped: it is not found again, whatever time is asked after it.
    assert leases.live("p1", 1000.0) is None


def test_the_ttl_is_45_seconds_when_none_is_named_and_at_most_90() -> None:
    assert lease_seconds(None) == DEFAULT_TTL_S == 45.0
    assert lease_seconds(10.0) == 10.0
    assert lease_seconds(90.0) == 90.0
    assert lease_seconds(120.0) == MAX_TTL_S == 90.0
    clock = _Clock(0.0)
    leases = PhoneLeases(clock)
    named = leases.renew("p1", "foreground", None, None)
    long = leases.renew("p2", "foreground", None, 3600.0)
    assert named is not None and named.expires_at == 45.0
    assert long is not None and long.expires_at == 90.0
    clock.now = 89.9
    assert leases.live("p1") is None and leases.live("p2") is not None
    clock.now = 90.0
    assert leases.live("p2") is None


def test_saying_it_again_moves_the_end_and_background_ends_the_lease_at_once() -> None:
    clock = _Clock(0.0)
    leases = PhoneLeases(clock)
    leases.renew("p1", "foreground", "thr_a", 45.0)
    clock.now = 25.0
    again = leases.renew("p1", "foreground", "thr_b", 45.0)
    assert again is not None and again.expires_at == 70.0 and again.chat == "thr_b"
    clock.now = 50.0
    held = leases.live("p1")
    assert held is not None and held.chat == "thr_b"
    assert leases.renew("p1", "background", None, None) is None
    assert leases.live("p1") is None


def test_each_phone_has_a_lease_of_its_own_and_a_removed_phone_has_none() -> None:
    clock = _Clock(0.0)
    leases = PhoneLeases(clock)
    leases.renew("p1", "foreground", "thr_a", None)
    leases.renew("p2", "foreground", None, None)
    assert leases.renew("p1", "background", None, None) is None
    assert leases.live("p1") is None and leases.live("p2") is not None
    leases.forget("p2")
    assert leases.live("p2") is None
    # Forgetting a phone that holds none, and ending one that holds none, are no errors.
    leases.forget("p3")
    assert leases.renew("p3", "background", None, None) is None
