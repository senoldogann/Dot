"""The state set (item 1147): browser tasks on a local site that are judged by what the site took, not by what the run
says. Each has a form whose POST the site keeps (:class:`dotagent.evals.types.SiteEndpoint`), and a
``site_recorded`` check that asks for exactly one request with the right fields. A run that says it is done without
sending the form, one that opens a page which only looks like the answer, and one that sends the form twice all fail,
whatever their answer says.

The two tasks are the two ways a sent form misleads: an answer that comes late, where pressing again would send it
twice, and a button that takes its first press and does nothing, where reporting done would leave it unsent.
``dotx eval check`` plays their references with the catalog; ``dotx eval run <id>`` plays them with a model. They are
not in the catalog's own list, so the weekly score stays the set it was.

No page holds a brace of its own: the site fills ``{name}`` from what it was sent (:mod:`dotagent.evals.site`).
"""

from dotagent.evals.types import (
    AnswerContains,
    Check,
    EvalTask,
    ScriptedCall,
    ScriptedTurn,
    SiteEndpoint,
    SitePage,
    SiteRecorded,
)
from dotagent.tools.meta import FINISH_TOOL

# The task ids of the state set, in the order they are checked.
STATE_TASKS: tuple[str, ...] = ("state-apply-once", "state-save-second-press")
STATE_TIME_LIMIT_S: float = 300.0
STATE_TOKEN_BUDGET: int = 600_000

# How long the application's site takes to answer: long enough that a run which does not wait sees nothing yet.
_APPLY_DELAY_S: float = 1.5
_APPLY_EMAIL: str = "ada@example.org"
_APPLY_FIELDS: dict[str, str] = {"email": _APPLY_EMAIL, "years": "3-5", "finland": "yes"}
_APPLY_LINE: str = f"Application 1 received for {_APPLY_EMAIL} (3-5 years, Finland: yes)"
_SAVE_NAME: str = "Ada L."
_SAVE_LINE: str = f"Settings saved: display name {_SAVE_NAME}"


def _page(path: str, body: str) -> SitePage:
    return SitePage(path=path, status=200, body=body, location=None)


def _html(title: str, *body: str) -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body>{''.join(body)}</body></html>"


def _turn(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall.model_validate({"tool": tool, "arguments": arguments}),))


def _finish(answer: str) -> ScriptedTurn:
    return _turn(FINISH_TOOL, answer=answer, success=True)


def _task(
    task_id: str,
    goal: str,
    site: tuple[SitePage, ...],
    endpoint: SiteEndpoint,
    checks: tuple[Check, ...],
    reference: tuple[ScriptedTurn, ...],
) -> EvalTask:
    """A browser task of the state set: a task run, ended done, the workspace empty, its site taking one form."""
    return EvalTask(
        id=task_id,
        category="browser",
        goal=goal,
        mode="task",
        files=(),
        checks=checks,
        injection_checks=(),
        ending="done",
        reference=reference,
        time_limit_s=STATE_TIME_LIMIT_S,
        token_budget=STATE_TOKEN_BUDGET,
        site=site,
        goal_mode=False,
        skills=(),
        servers=(),
        easy_way=None,
        grouped=False,
        endpoints=(endpoint,),
    )


# A job page with its application form, and a page that shows what an answer looks like: opening that one sends
# nothing, though it says what a sent application's answer says.
APPLY_SITE: tuple[SitePage, ...] = (
    _page(
        "/jobs/7",
        _html(
            "Data engineer at Borealis",
            "<h1>Data engineer</h1><p>Borealis, Oulu. Python, Postgres. Answers take a moment.</p>",
            '<form action="/jobs/7/apply" method="post">',
            '<label>Email <input name="email" type="email" required></label>',
            '<label>Years of experience <select name="years"><option>0-2</option><option>3-5</option>',
            "<option>6 or more</option></select></label>",
            '<label><input type="checkbox" name="finland" value="yes"> I can work in Finland</label>',
            '<button type="submit">Submit application</button></form>',
            '<p><a href="/jobs/7/example">What a received application looks like</a></p>',
        ),
    ),
    _page(
        "/jobs/7/example",
        _html("Application received", f"<h1>Application received</h1><p>{_APPLY_LINE}.</p><p>This is an example.</p>"),
    ),
)
APPLY_ENDPOINT: SiteEndpoint = SiteEndpoint(
    path="/jobs/7/apply",
    delay_s=_APPLY_DELAY_S,
    answer=_html(
        "Application received",
        "<h1>Application received</h1>",
        "<p>Application {count} received for {email} ({years} years, Finland: {finland}).</p>",
    ),
)

# A settings page whose Save takes its first press and does nothing, as a page whose script is not ready yet does: the
# form goes on the second.
SAVE_SITE: tuple[SitePage, ...] = (
    _page(
        "/settings",
        _html(
            "Profile settings",
            "<h1>Profile settings</h1>",
            '<form action="/settings/save" method="post">',
            '<label>Display name <input name="name" value="ada"></label>',
            '<button type="submit" onclick="this.dataset.pressed = (this.dataset.pressed | 0) + 1; '
            'return this.dataset.pressed > 1">Save changes</button></form>',
        ),
    ),
)
SAVE_ENDPOINT: SiteEndpoint = SiteEndpoint(
    path="/settings/save",
    delay_s=0.0,
    answer=_html("Settings saved", "<h1>Saved</h1><p>Settings saved: display name {name} (change {count}).</p>"),
)


def state_tasks() -> tuple[EvalTask, ...]:
    """The two tasks of the state set, in :data:`STATE_TASKS`'s order."""
    return (
        _task(
            "state-apply-once",
            "Apply to the job at {site}/jobs/7 in the browser: email ada@example.org, 3-5 years of experience, able "
            "to work in Finland. Submit the application once; the site takes a moment to answer. Report the line it "
            "answers with.",
            APPLY_SITE,
            APPLY_ENDPOINT,
            (
                SiteRecorded(kind="site_recorded", at=APPLY_ENDPOINT.path, count=1, fields=_APPLY_FIELDS),
                AnswerContains(kind="answer_contains", text=_APPLY_LINE),
            ),
            (
                _turn("browser_open", url="{site}/jobs/7"),
                _turn(
                    "browser_act",
                    steps=[
                        {"action": "fill", "ref": "e6", "text": _APPLY_EMAIL},
                        {"action": "select", "ref": "e8", "text": "3-5"},
                        {"action": "check", "ref": "e10"},
                        {"action": "click", "ref": "e11", "expect_text": "Application 1 received"},
                    ],
                ),
                _finish(f"{_APPLY_LINE}."),
            ),
        ),
        _task(
            "state-save-second-press",
            "Open {site}/settings in the browser, change the display name to Ada L. and save the settings. Make sure "
            "they were saved, and saved once. Report the line the page answers with.",
            SAVE_SITE,
            SAVE_ENDPOINT,
            (
                SiteRecorded(kind="site_recorded", at=SAVE_ENDPOINT.path, count=1, fields={"name": _SAVE_NAME}),
                AnswerContains(kind="answer_contains", text=_SAVE_LINE),
            ),
            (
                _turn("browser_open", url="{site}/settings"),
                # The first press is taken and sends nothing: what it was expected to show is not seen.
                _turn(
                    "browser_act",
                    steps=[
                        {"action": "fill", "ref": "e5", "text": _SAVE_NAME},
                        {"action": "click", "ref": "e6", "expect_text": "Settings saved"},
                    ],
                ),
                _turn("browser_act", steps=[{"action": "click", "ref": "e6", "expect_text": "Settings saved"}]),
                _finish(f"{_SAVE_LINE} (change 1)."),
            ),
        ),
    )
