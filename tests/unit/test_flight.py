"""The flash and the flight of a capture of SnapShots (item 414), as far as they are numbers: how long a flight takes,
where the card lands in Dot's window or in the corner of the screen, how a frame from the top left reads in AppKit's
terms, the helper's commands, and the order the browsers are asked for Dot's tab in."""

from dotagent.desktop.flight import (
    FLIGHT_MAX_MS,
    FLIGHT_MIN_MS,
    FlightCommand,
    Frame,
    corner,
    distance,
    flight_ms,
    landing,
    read_command,
    to_appkit,
)
from dotagent.desktop.show_dot import browsers_first


def test_a_flight_is_short_for_a_hop_and_at_most_its_bound_across_the_screen() -> None:
    assert flight_ms(0.0) == FLIGHT_MIN_MS
    assert FLIGHT_MIN_MS < flight_ms(800.0) < flight_ms(2400.0) < FLIGHT_MAX_MS
    assert flight_ms(1_000_000.0) == FLIGHT_MAX_MS


def test_the_card_lands_at_the_foot_of_dots_window_or_in_the_corner_in_the_pictures_proportions() -> None:
    dot: Frame = Frame(x=900.0, y=40.0, width=1000.0, height=900.0)
    landed: Frame = landing(dot, 0.5)
    assert (landed.width, landed.height) == (180.0, 90.0)
    assert landed.x == 900.0 + (1000.0 - 180.0) / 2
    assert landed.y + landed.height == 40.0 + 900.0 - 120.0
    # A narrow window takes a card of a third of its width.
    assert landing(Frame(x=0.0, y=0.0, width=300.0, height=600.0), 1.0).width == 100.0
    screen: Frame = Frame(x=0.0, y=0.0, width=1440.0, height=900.0)
    cornered: Frame = corner(screen, 0.5)
    assert (cornered.x + cornered.width, cornered.y + cornered.height) == (1440.0 - 24.0, 900.0 - 24.0)
    assert (
        distance(Frame(x=0.0, y=0.0, width=10.0, height=10.0), Frame(x=30.0, y=40.0, width=10.0, height=10.0)) == 50.0
    )


def test_a_frame_from_the_top_left_is_placed_from_the_bottom_left_of_the_main_screen() -> None:
    assert to_appkit(Frame(x=10.0, y=100.0, width=300.0, height=200.0), 900.0) == Frame(
        x=10.0, y=600.0, width=300.0, height=200.0
    )


def test_the_helper_reads_its_commands_a_line_each_and_leaves_out_one_it_cannot_read() -> None:
    sent: FlightCommand = FlightCommand(
        op="begin", frame=Frame(x=1.0, y=2.0, width=3.0, height=4.0), picture="/tmp/a.jpg", flash=True, fly=False
    )
    assert read_command(sent.model_dump_json()) == sent
    assert read_command('{"op": "fly"}') is None


def test_the_browser_of_the_page_is_asked_first_for_dots_tab_and_only_running_browsers_are() -> None:
    running: list[str] = ["com.apple.Safari", "com.google.Chrome", "company.thebrowser.Browser", "com.apple.finder"]
    chrome: str = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    safari: str = "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
    assert browsers_first(chrome, running) == ["com.google.Chrome", "company.thebrowser.Browser", "com.apple.Safari"]
    assert browsers_first(safari, running) == ["com.apple.Safari", "com.google.Chrome", "company.thebrowser.Browser"]
    assert browsers_first(None, running) == ["com.apple.Safari", "com.google.Chrome", "company.thebrowser.Browser"]
    assert browsers_first(chrome, ["com.apple.finder"]) == []
