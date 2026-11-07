"""The roles of the improvement loop (item 390, docs/harness.md), each a question to a model that answers with one
tool call (:mod:`dotagent.agent.review`): the proposer, which writes a candidate from what a run did; the task writer,
which makes that run a task of files (item 904); the composer, which makes one version of the candidates and what their
probes showed; the selector, which picks among anonymous answers; and the experience writer, which records what each
edit did. ``[harness]`` names a model for each, or leaves it to the review model; the task writer is the proposer's."""

from collections.abc import Awaitable, Callable

from dotagent.agent.review import (
    ExperienceWriter,
    HarnessComposer,
    HarnessProposer,
    HarnessSelector,
    HarnessTaskAnswer,
    TaskRequest,
    experience_writer_for,
    harness_composer_for,
    harness_proposer_for,
    harness_selector_for,
    harness_task_writer_for,
)
from dotagent.llm.roles import LEARNING_ROLES, LearningRole, ModelRoles
from dotagent.runtime import Runtime

type RoleName = LearningRole
ROLE_NAMES: tuple[RoleName, ...] = LEARNING_ROLES


def role_model(roles: ModelRoles, role: RoleName) -> str:
    """The model that plays ``role``: the one named for it (``[harness]``, or Settings › Models since item 1033), else
    the review model."""
    return roles.learning_model(role)


def proposer_of(runtime: Runtime, session_key: str, run_id: str | None) -> HarnessProposer:
    """The proposer, asked on the thread ``session_key`` names, about the run ``run_id`` (``None`` outside any run)."""
    return harness_proposer_for(
        runtime.router,
        role_model(runtime.roles, "proposer"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def task_writer_of(
    runtime: Runtime, session_key: str, run_id: str
) -> Callable[[TaskRequest], Awaitable[HarnessTaskAnswer]]:
    """The task writer (item 904), on the proposer's model: it reads the run the proposer reads, and a weak model
    writes a task that the plays refuse, at the cost of nothing but the call."""
    return harness_task_writer_for(
        runtime.router,
        role_model(runtime.roles, "proposer"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def composer_of(runtime: Runtime, session_key: str, run_id: str | None) -> HarnessComposer:
    return harness_composer_for(
        runtime.router,
        role_model(runtime.roles, "composer"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def selector_of(runtime: Runtime, session_key: str, run_id: str | None) -> HarnessSelector:
    return harness_selector_for(
        runtime.router,
        role_model(runtime.roles, "selector"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def experience_writer_of(runtime: Runtime, session_key: str, run_id: str | None) -> ExperienceWriter:
    return experience_writer_for(
        runtime.router,
        role_model(runtime.roles, "experience"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )
