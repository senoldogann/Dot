"""Where the repairs' pull requests stand on GitHub, read again (item 958), against a stand-in of ``gh`` on this Mac: a
problem that waited on a merged pull request is fixed and one on a closed one closed, each session takes its pull
request's state, an open one stays waiting, a failed ask is asked again, and what is merged or closed is not read
again. Nothing outside this Mac is reached."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dotagent.core.errors import RepairError
from dotagent.repair.night import SESSIONS_KEY, RepairSession, SessionLog, read_sessions
from dotagent.repair.problems import BOOK_KEY, Book, Problem, read_book
from dotagent.repair.settle import settle_stored
from dotagent.store.db import Store

pytestmark = pytest.mark.asyncio

_URL: str = "https://github.com/senoldogann/Dot/pull/{}"
_MERGED_AT: float = datetime(2026, 11, 3, 6, 0, tzinfo=UTC).timestamp()
# The stand-in of ``gh``: ``pr view <n> --json state`` answers the state the file ``states`` gives the number, and fails
# once for the numbers the file ``flaky`` names; every ask is written down.
_GH: str = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "asked").open("a") as asked:
    asked.write(" ".join(args) + "\\n")
number = args[2]
flaky = here / f"flaky-{number}"
if flaky.exists():
    flaky.unlink()
    sys.exit("gh: could not reach GitHub")
if number == "999":
    sys.exit("gh: no pull request found")
state = json.loads((here / "states").read_text())[number]
at = "2026-11-03T06:00:00Z"
print(json.dumps({"state": state, "mergedAt": at if state == "MERGED" else None,
                  "closedAt": at if state != "OPEN" else None}))
"""


def _problem(key: str, number: int, status: str) -> Problem:
    return Problem(
        id=key, kind="tool_error", title=f"problem {key}", evidence=("e",), count=3, first_at=1.0, last_at=2.0,
        status=status, tries=1, pr_url=_URL.format(number), note="",
    )  # fmt: skip


def _session(key: str, number: int, end: str) -> RepairSession:
    return RepairSession(
        problem_id=key, account="a@b.dev", session_id=None, started_at=1.0, ended_at=2.0, rounds=1, input_tokens=0,
        output_tokens=0, cost_usd=0.0, end=end, pr_url=_URL.format(number), note="", window=None,
    )  # fmt: skip


async def _no_wait(_seconds: float) -> None:
    return None


def _stand_in(bin_dir: Path) -> None:
    bin_dir.mkdir()
    gh: Path = bin_dir / "gh"
    gh.write_text(_GH, encoding="utf-8")
    gh.chmod(0o755)
    (bin_dir / "states").write_text(json.dumps({"764": "MERGED", "768": "CLOSED", "770": "OPEN"}), encoding="utf-8")
    (bin_dir / "flaky-768").touch()


def _asked(bin_dir: Path) -> list[str]:
    """The numbers of the pull requests ``gh`` was asked about, in order."""
    return [line.split()[2] for line in (bin_dir / "asked").read_text(encoding="utf-8").splitlines()]


async def test_merged_and_closed_pull_requests_settle_their_problems_and_sessions(tmp_path: Path) -> None:
    bin_dir: Path = tmp_path / "bin"
    _stand_in(bin_dir)
    env: dict[str, str] = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        book = Book(
            problems=(_problem("a", 764, "waiting"), _problem("b", 768, "waiting"), _problem("c", 770, "waiting")),
            looked_at=None,
        )
        # 999 never answers: the others are read and kept all the same, and the reading says it failed.
        sessions = (
            _session("a", 764, "needs_owner"),
            _session("b", 768, "main_moved"),
            _session("c", 770, "ready"),
            _session("d", 999, "ready"),
        )
        await store.kv_set(BOOK_KEY, book.model_dump_json())
        await store.kv_set(SESSIONS_KEY, SessionLog(sessions=sessions).model_dump_json())
        with pytest.raises(RepairError, match="could not be asked") as unread:
            await settle_stored(store, tmp_path, env, _no_wait)
        assert list(unread.value.context["unread"]) == [_URL.format(999)]
        settled: Book = read_book(await store.kv_get(BOOK_KEY))
        kept: tuple[RepairSession, ...] = read_sessions(await store.kv_get(SESSIONS_KEY))
        assert [(p.status, p.pr_state) for p in settled.problems] == [
            ("fixed", "merged"),
            ("closed", "closed"),
            ("waiting", "open"),
        ]
        # Fixed since GitHub merged it, which is what a later sighting is read against.
        assert settled.problems[0].settled_at == _MERGED_AT and "merged on GitHub" in settled.problems[0].note
        assert [session.pr_state for session in kept] == ["merged", "closed", "open", None]
        # 768 failed once and was asked again; each pull request was read once more than that, no more.
        assert _asked(bin_dir) == ["764", "768", "768", "770", "999", "999", "999"]
        # What is merged or closed is not read again; the open one and the unread one are.
        with pytest.raises(RepairError):
            await settle_stored(store, tmp_path, env, _no_wait)
        assert _asked(bin_dir)[7:] == ["770", "999", "999", "999"]
    finally:
        await store.close()
