"""The state set (item 1147): a site that keeps what it was sent, and the check that reads it. A task is judged by
what its site took: as many requests as asked, each with the fields asked, whatever the answer of the run says."""

import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from dotagent.evals.audit import catalog_problems, task_problems
from dotagent.evals.catalog import all_tasks, select_tasks
from dotagent.evals.checks import describe_check, evaluate_check
from dotagent.evals.results import CheckOutcome
from dotagent.evals.site import held_now, render_answer, render_page, serve_recording_site, serve_site
from dotagent.evals.state_tasks import APPLY_ENDPOINT, APPLY_SITE, STATE_TASKS, state_tasks
from dotagent.evals.types import EvalTask, SiteEndpoint, SitePage, SiteRecord, SiteRecorded

ENV: dict[str, str] = {}
_ONCE: SiteRecorded = SiteRecorded(
    kind="site_recorded", at="/jobs/7/apply", count=1, fields={"email": "ada@example.org", "years": "3-5"}
)
_SENT: dict[str, str] = {"email": "ada@example.org", "years": "3-5", "finland": "yes"}


def _post(address: str, path: str, fields: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(address + path, data=urllib.parse.urlencode(fields).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=10) as answer:
        return answer.status, answer.read().decode("utf-8")


async def _holds(check: SiteRecorded, records: list[SiteRecord]) -> CheckOutcome:
    return await evaluate_check(check, Path("/nonexistent"), "Application 1 received", ENV, (), records)


def test_a_site_keeps_what_its_endpoint_was_sent_and_a_page_keeps_nothing() -> None:
    quick: SiteEndpoint = APPLY_ENDPOINT.model_copy(update={"delay_s": 0.0})
    with serve_recording_site(APPLY_SITE, (quick,)) as served:
        assert served.taken() == ()
        with urllib.request.urlopen(served.address + "/jobs/7/example", timeout=10) as page:
            assert "Application 1 received" in page.read().decode("utf-8")
        # The page that looks like an answer was opened, and the site took nothing.
        assert served.taken() == ()
        status, first = _post(served.address, "/jobs/7/apply", _SENT)
        assert status == 200
        assert "Application 1 received for ada@example.org (3-5 years, Finland: yes)." in first
        _, second = _post(served.address, "/jobs/7/apply", {"email": "b@example.org"})
        assert "Application 2 received for b@example.org ( years, Finland: )." in second
        assert served.taken() == (
            SiteRecord(path="/jobs/7/apply", fields=_SENT),
            SiteRecord(path="/jobs/7/apply", fields={"email": "b@example.org"}),
        )


def test_a_page_shows_what_its_site_took_last_and_nothing_before_it_took_any() -> None:
    """Item 1156: the settings page showed its old name whatever was saved, and runs went looking for the server's
    state another way. A page may name a field of what its site holds: what it held at first, then what the newest
    request sent."""
    name: SitePage = SitePage(path="/settings", status=200, body="<p>On file: {last_name}, as {q}</p>", location=None)
    saved: SiteRecord = SiteRecord(path="/settings/save", fields={"name": "Ada L."})
    older: SiteRecord = SiteRecord(path="/settings/save", fields={"name": "Ada"})
    save: SiteEndpoint = SiteEndpoint(path="/settings/save", delay_s=0.0, answer="saved {name}", holds={"name": "ada"})
    bare: SiteEndpoint = SiteEndpoint(path="/other", delay_s=0.0, answer="")
    assert held_now((bare,), ()) == {}
    assert held_now((bare, save), ()) == {"name": "ada"}
    assert held_now((bare, save), (older, saved)) == {"name": "Ada L."}
    assert render_page(name, "q=asked", {}) == "<p>On file: , as asked</p>"
    assert render_page(name, "q=asked", {"name": "Ada L."}) == "<p>On file: Ada L., as asked</p>"
    # What was sent is shown as text: a quote or a tag in it does not become the page's own.
    assert render_page(name, "q=asked", {"name": 'Ada "L" <b>'}) == (
        "<p>On file: Ada &quot;L&quot; &lt;b&gt;, as asked</p>"
    )
    assert render_answer(save, {"name": "<Ada>"}, 1) == "saved &lt;Ada&gt;"
    with serve_recording_site((name,), (save,)) as served:
        with urllib.request.urlopen(served.address + "/settings", timeout=10) as page:
            assert "On file: ada," in page.read().decode("utf-8")
        _post(served.address, "/settings/save", {"name": "Ada L."})
        with urllib.request.urlopen(served.address + "/settings", timeout=10) as page:
            assert "On file: Ada L." in page.read().decode("utf-8")


def test_a_site_without_endpoints_is_served_as_before_and_takes_no_post() -> None:
    with serve_site(APPLY_SITE) as address:
        with pytest.raises(urllib.error.HTTPError) as refused:
            _post(address, "/jobs/7/apply", _SENT)
        assert refused.value.code == 404
    with serve_recording_site((), ()) as nothing:
        assert (nothing.address, nothing.taken()) == ("", ())


async def test_the_check_holds_for_exactly_as_many_requests_with_the_fields_it_names() -> None:
    once: list[SiteRecord] = [SiteRecord(path="/jobs/7/apply", fields=_SENT)]
    assert (await _holds(_ONCE, once)).passed
    # A request elsewhere on the site is no request there.
    assert (await _holds(_ONCE, [*once, SiteRecord(path="/other", fields={})])).passed
    none: CheckOutcome = await _holds(_ONCE, [])
    assert (none.passed, none.detail) == (False, "the site took 0 there")
    twice: CheckOutcome = await _holds(_ONCE, [*once, *once])
    assert (twice.passed, twice.detail) == (False, "the site took 2 there")
    wrong: CheckOutcome = await _holds(_ONCE, [SiteRecord(path="/jobs/7/apply", fields={**_SENT, "years": "0-2"})])
    assert not wrong.passed and "1 with other fields" in wrong.detail
    assert describe_check(_ONCE) == (
        'the site took 1 request at /jobs/7/apply with {"email": "ada@example.org", "years": "3-5"}'
    )


def test_the_state_set_is_sound_and_stays_out_of_the_catalogs_own_list() -> None:
    tasks: tuple[EvalTask, ...] = state_tasks()
    assert tuple(task.id for task in tasks) == STATE_TASKS
    assert catalog_problems((*all_tasks(), *tasks)) == ()
    assert not {task.id for task in all_tasks()} & set(STATE_TASKS)
    assert select_tasks(STATE_TASKS) == tasks
    for task in tasks:
        assert any(isinstance(check, SiteRecorded) and check.count == 1 for check in task.checks), task.id


def test_a_check_of_a_path_the_site_does_not_take_is_a_mistake_of_the_task() -> None:
    task: EvalTask = state_tasks()[0]
    adrift: EvalTask = task.model_copy(update={"endpoints": ()})
    assert task_problems(adrift) == ("the check of the site at '/jobs/7/apply' names no endpoint of the task",)
