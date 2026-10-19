"""The roles of the improvement loop (item 390, docs/harness.md), each a question to a model that answers with one
tool call (:mod:`dotagent.agent.review`): the proposer, which writes a candidate from what a run did; the composer,
which makes one version of the candidates and what their probes showed; the selector, which picks among anonymous
answers; and the experience writer, which records what each edit did. ``[harness]`` names a model for each, or leaves
it to the review model."""

from typing import Literal

from dotagent.agent.review import (
    ExperienceWriter,
    HarnessComposer,
    HarnessProposer,
    HarnessSelector,
    experience_writer_for,
    harness_composer_for,
    harness_proposer_for,
    harness_selector_for,
)
from dotagent.config import HarnessConfig
from dotagent.llm.roles import ModelRoles
from dotagent.runtime import Runtime

type RoleName = Literal["proposer", "composer", "selector", "experience"]
ROLE_NAMES: tuple[RoleName, ...] = ("proposer", "composer", "selector", "experience")


def role_model(config: HarnessConfig, roles: ModelRoles, role: RoleName) -> str:
    """The model that plays ``role``: the one ``[harness]`` names, else the review model."""
    named: str = {
        "proposer": config.proposer,
        "composer": config.composer,
        "selector": config.selector,
        "experience": config.experience,
    }[role]
    return named if named != "" else roles.review()


def proposer_of(runtime: Runtime, session_key: str, run_id: str | None) -> HarnessProposer:
    """The proposer, asked on the thread ``session_key`` names, about the run ``run_id`` (``None`` outside any run)."""
    return harness_proposer_for(
        runtime.router,
        role_model(runtime.config.harness, runtime.roles, "proposer"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def composer_of(runtime: Runtime, session_key: str, run_id: str | None) -> HarnessComposer:
    return harness_composer_for(
        runtime.router,
        role_model(runtime.config.harness, runtime.roles, "composer"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def selector_of(runtime: Runtime, session_key: str, run_id: str | None) -> HarnessSelector:
    return harness_selector_for(
        runtime.router,
        role_model(runtime.config.harness, runtime.roles, "selector"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )


def experience_writer_of(runtime: Runtime, session_key: str, run_id: str | None) -> ExperienceWriter:
    return experience_writer_for(
        runtime.router,
        role_model(runtime.config.harness, runtime.roles, "experience"),
        runtime.config.agent.model_call_timeout_s,
        session_key,
        run_id,
    )
