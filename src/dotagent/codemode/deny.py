"""The tools a code cannot call (item 1501).

Everything the run's mode and its tool list allow is callable from code, and each call is judged by the run's own gate.
What is not callable is what makes no sense, or no safety, from inside a program that runs while the run waits: ending
the run, asking the owner and waiting for the answer, starting or steering another run, agent, chat or call, the plan,
and code starting code. The list is in code, one reason for each name, which the code is told when it calls one; a test
names each, and holds that each is a tool of Dot that exists, so that a rename cannot drop one from the list unseen.
"""

from collections.abc import Mapping

from dotagent.codemode.inner import CODE_RUN_TOOL
from dotagent.llm.types import ToolSpec

_ENDS_THE_RUN: str = "only the model's own turn ends the run"
_ASKS_THE_OWNER: str = "a question waits for the owner's answer: ask it yourself, outside the code"
_STARTS_RUNS: str = "it starts or steers another run, agent or chat: do it yourself, outside the code"
_CAMPAIGN: str = "it belongs to a campaign's own loop, which the code is not part of"
_OWN_LOOP: str = "it is the run's own: the plan, the groups and the reactions are the model's to keep"

NOT_CALLABLE: dict[str, str] = {
    CODE_RUN_TOOL: "code cannot start more code",
    "finish": _ENDS_THE_RUN,
    "ask_user": _ASKS_THE_OWNER,
    "ask_secret": _ASKS_THE_OWNER,
    "browser_captcha": "it hands a step to the owner: ask for it yourself, outside the code",
    "use_tools": "tool groups are the model's own list: find tools with dot_tools.search_tools",
    "update_plan": _OWN_LOOP,
    "mark_plan_step": _OWN_LOOP,
    "react": _OWN_LOOP,
    "delegate": _STARTS_RUNS,
    "consult": "it asks another model and waits for it: do it yourself, outside the code",
    "message_agent": _STARTS_RUNS,
    "assign_work": _STARTS_RUNS,
    "work_orders": _STARTS_RUNS,
    "review_work": _STARTS_RUNS,
    "create_agent": _STARTS_RUNS,
    "create_team": _STARTS_RUNS,
    "send_to_chat": _STARTS_RUNS,
    "pass_reply": _STARTS_RUNS,
    "schedule_create": "it makes a run that starts later: do it yourself, outside the code",
    "wait_until": "it holds the call for as long as it waits: poll in the code with time.sleep",
    "campaign_achieved": _CAMPAIGN,
    "campaign_note": _CAMPAIGN,
    "campaign_schedule": _CAMPAIGN,
    "campaign_targets": _CAMPAIGN,
}

# A family of tools by the start of its names: a terminal session, a call the owner is in, the owner's iPhone.
NOT_CALLABLE_PREFIXES: dict[str, str] = {
    "terminal_": "a terminal session stays open and waits for what it is sent: use it yourself, outside the code",
    "meeting_": "it joins or speaks in a call the owner is in: do it yourself, outside the code",
    "phone_": "it drives the owner's phone: do it yourself, outside the code",
}


def refusal_for(name: str) -> str | None:
    """Why the tool ``name`` cannot be called from code, or ``None`` when it can."""
    reason: str | None = NOT_CALLABLE.get(name)
    if reason is not None:
        return reason
    return next((why for prefix, why in NOT_CALLABLE_PREFIXES.items() if name.startswith(prefix)), None)


def callable_from_code(specs: Mapping[str, ToolSpec]) -> dict[str, ToolSpec]:
    """The tools of ``specs`` that a code may call: all but those this module names."""
    return {name: spec for name, spec in specs.items() if refusal_for(name) is None}
