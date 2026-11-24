"""The hop limit's rule (item 1741): above ``[agent] max_hops`` hand-offs a hand-off is not made, and 0 turns the limit
off. The default is above what real chains did on the owner's Mac (89 hand-offs in 530 runs, none more than one deep,
which the delegate-only structure of a helper and the six-notes cap of a run keep shallow)."""

from dotagent.agent.limits import over_the_hop_limit
from dotagent.config import load_default_config


def test_the_limit_allows_its_own_hop_and_stops_the_next() -> None:
    assert not over_the_hop_limit(8, 8)
    assert over_the_hop_limit(9, 8)
    assert not over_the_hop_limit(0, 8)


def test_zero_turns_the_limit_off() -> None:
    assert not over_the_hop_limit(10_000, 0)


def test_the_default_is_eight_and_leaves_room_over_the_deepest_chain_measured() -> None:
    deepest_measured: int = 2  # a lead, the agent it messaged and the lead woken by its answer
    assert load_default_config().agent.max_hops == 8
    assert not over_the_hop_limit(deepest_measured * 3, load_default_config().agent.max_hops)
