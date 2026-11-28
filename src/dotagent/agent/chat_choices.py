"""The chat's own choices for a message that leaves them out (item 2071).

A chat keeps what its composer shows for its next message (``ChatChoices``, item 199): the model the owner picked, its
reasoning level, the speed, the approval mode and goal mode. The web composer sends them with every message; the
iPhone's follow-up sends none of them, and a message that named no model started on the daemon's default model, not the
one the chat showed (a chat on Claude Haiku 5.5 ran three times on DeepSeek). So a message to a chat takes each choice
it leaves out from the chat, and a choice it names, ``null`` too (the daemon's default), stays its own. A resume starts
on the chat's model as well, when the chat names another model than the run it resumes: the owner picked it since.
"""

from collections.abc import Set

from dotagent.core.types import FrozenModel
from dotagent.llm.types import Effort, Speed
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import ChatChoices, ChatMode


class MessageChoices(FrozenModel):
    """The choices one message starts its run with, as its request names them (``None`` takes the daemon's)."""

    mode: ChatMode
    model: str | None
    effort: Effort | None
    speed: Speed
    approvals: ApprovalMode | None
    goal_mode: bool | None


class ResumeChoices(FrozenModel):
    """The model, reasoning level and speed a resume starts with."""

    model: str
    effort: Effort | None
    speed: Speed


def message_choices(sent: MessageChoices, named: Set[str], kept: ChatChoices | None) -> MessageChoices:
    """The choices of a message to a chat that keeps ``kept``: each one the request named (``named``, the fields it
    set) is the request's, each one it left out is the chat's. The level and the speed go with the model: a message
    that names a model keeps its own level and speed, which are that model's, not the chat's model's. A chat that keeps
    none (its runs began before chats kept them) leaves the message as it is."""
    if kept is None:
        return sent
    own_model: bool = "model" in named
    return MessageChoices(
        mode=sent.mode if "mode" in named else kept.mode,
        model=sent.model if own_model else kept.model,
        effort=sent.effort if own_model or "effort" in named else kept.effort,
        speed=sent.speed if own_model or "speed" in named else kept.speed,
        approvals=sent.approvals if "approvals" in named else kept.approvals,
        goal_mode=sent.goal_mode if "goal_mode" in named else kept.goal_mode,
    )


def resume_choices(model: str, effort: Effort | None, speed: Speed, kept: ChatChoices | None) -> ResumeChoices:
    """The model, level and speed a resume of a run on ``model`` (with ``effort`` and ``speed``) starts with in a chat
    that keeps ``kept``: the chat's model when it names another one, with the chat's level and speed; else the run's
    own. A chat that keeps none, or keeps the daemon's default (``None``), leaves the run's: the default is no model
    the owner picked over the one the run worked on."""
    if kept is None or kept.model is None or kept.model == model:
        return ResumeChoices(model=model, effort=effort, speed=speed)
    return ResumeChoices(model=kept.model, effort=kept.effort, speed=kept.speed)
