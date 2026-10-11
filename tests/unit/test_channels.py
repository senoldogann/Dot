"""The channel of a tool and the GUI share of the calls (item 27)."""

import pytest

from dotagent.store.stats import ChannelStats, ToolSample, channel_stats, gui_share
from dotagent.tools.channels import Channel, channel_of


@pytest.mark.parametrize(
    ("tool", "channel"),
    [
        ("shell", "command"),
        ("terminal_send", "command"),
        ("read_file", "command"),
        ("mcp__github__list_issues", "api"),
        ("apple_script", "api"),
        ("code_fetch", "api"),
        ("web_fetch", "web"),
        ("web_crawl", "web"),
        ("browser_act", "browser"),
        ("desktop_act", "desktop"),
        ("update_plan", "agent"),
        ("memory_save", "agent"),
        ("delegate", "agent"),
    ],
)
def test_the_channel_follows_the_tool(tool: str, channel: Channel) -> None:
    assert channel_of(tool) == channel


def test_the_gui_share_counts_the_browser_and_the_desktop_among_the_acting_calls() -> None:
    def sample(tool: str, ok: bool) -> ToolSample:
        return ToolSample(model="ollama:a", tool=tool, ok=ok, seconds=1.0, waited_s=0.0, text="")

    channels: tuple[ChannelStats, ...] = channel_stats(
        [
            sample("shell", True),
            sample("web_fetch", True),
            sample("browser_act", False),
            sample("browser_act", True),
            sample("desktop_act", True),
            sample("update_plan", True),
            sample("finish", True),
        ]
    )
    assert [(entry.channel, entry.calls, entry.failed) for entry in channels] == [
        ("command", 1, 0),
        ("web", 1, 0),
        ("browser", 2, 1),
        ("desktop", 1, 0),
        ("agent", 2, 0),
    ]
    assert gui_share(channels) == pytest.approx(3 / 5)
    assert gui_share(channel_stats([sample("finish", True)])) is None
    assert gui_share(()) is None
