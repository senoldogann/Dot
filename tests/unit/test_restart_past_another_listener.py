"""A restart of the daemon while another program listens on every address of its port (item 970).

The owner's Mac had ``python3 -m http.server 8765`` listening on ``*:8765``: in the moment between the old daemon's end
and the new one binding ``127.0.0.1:8765``, the health check reached that server, whose 404 page the restart took for
its final answer, so ``restart_when_idle`` logged ``restart_when_idle_failed`` although the daemon came up a moment
later. launchd and the health check are stand-ins here: nothing of this Mac's launchd or daemon is touched."""

from pathlib import Path

import pytest

from dotagent import cli_daemon
from dotagent.cli_daemon import restart_service, wait_for_daemon
from dotagent.core.errors import ServiceError
from dotagent.launchd import JobState
from dotagent.server.views import HealthView

URL: str = "http://127.0.0.1:8765/api/health"
STRANGER: ServiceError = ServiceError(
    "something other than the Dot daemon answers at the address", {"url": URL, "status": 404}
)


@pytest.fixture
def launchd(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job whose process is replaced by the restart, and no wait between looks."""
    pids: list[int] = [100]

    async def inspect(uid: int) -> JobState:
        return JobState(state="running", pid=pids[-1], runs=1, last_exit=None)

    async def restart(plist: Path, uid: int) -> None:
        pids.append(200)

    monkeypatch.setattr(cli_daemon, "inspect_job", inspect)
    monkeypatch.setattr(cli_daemon, "restart_job", restart)
    monkeypatch.setattr(cli_daemon, "_POLL_S", 0.0)


def _health_check(monkeypatch: pytest.MonkeyPatch, looks: list[HealthView | ServiceError | None]) -> None:
    """The health check answers ``looks`` one by one, the last again once they are used up."""
    left: list[HealthView | ServiceError | None] = list(looks)

    async def probe(url: str) -> HealthView | None:
        look: HealthView | ServiceError | None = left.pop(0) if len(left) > 1 else left[0]
        if isinstance(look, ServiceError):
            raise look
        return look

    monkeypatch.setattr(cli_daemon, "probe_daemon", probe)


async def test_a_restart_waits_past_another_program_answering_until_the_daemon_does(
    launchd: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dot: HealthView = HealthView(ok=True, version="0.0.0-test")
    _health_check(monkeypatch, [STRANGER, STRANGER, dot])
    assert await restart_service(Path("dev.dotagent.dot.plist"), 501, URL) == dot


async def test_a_restart_that_only_another_program_answers_raises_its_answer_at_the_end(
    launchd: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_daemon, "_START_WAIT_S", 0.05)
    _health_check(monkeypatch, [STRANGER])
    with pytest.raises(ServiceError) as raised:
        await restart_service(Path("dev.dotagent.dot.plist"), 501, URL)
    assert raised.value is STRANGER


async def test_an_install_waits_past_another_program_answering_until_the_daemon_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_daemon, "_POLL_S", 0.0)
    dot: HealthView = HealthView(ok=True, version="0.0.0-test")
    _health_check(monkeypatch, [None, STRANGER, dot])
    assert await wait_for_daemon(URL, 5.0) == dot
