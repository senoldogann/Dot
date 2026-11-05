"""The half of a trial of the harness a run takes as it starts (item 988, docs/harness.md): the trial's candidate for
one half of the runs, by their id, the version in use for the other. Kept apart from :mod:`dotagent.harness.weekly`,
which the run's loop cannot import."""

from collections.abc import Sequence

from dotagent.core.errors import NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.harness.components import GivenText
from dotagent.harness.service import adopted_text, given_text
from dotagent.harness.trial import DAY_S, TRIAL_DAYS, TRIAL_KEY, Arm, Trial, arm_of
from dotagent.store.db import Store
from dotagent.store.models import HarnessVersion

log = get_logger(__name__)

# The event a run records its half of a trial in.
TRIAL_EVENT: str = "harness_trial"


async def trial_of(store: Store) -> Trial | None:
    """The trial under way, or ``None``."""
    kept: str | None = await store.kv_get(TRIAL_KEY)
    return None if not kept else Trial.model_validate_json(kept)


class Seat(FrozenModel):
    """The half of a trial a run is in: the trial's version and the run's half."""

    version: str
    arm: Arm


async def text_for_run(
    store: Store, run_id: str, held: Sequence[str], now: float
) -> tuple[GivenText, str | None, Seat | None]:
    """The text of the harness a run takes as it starts at ``now``, the version it is and, while a trial is under way,
    the half the run is in: the trial's candidate for one half, the version in use for the other. A trial past its
    :data:`TRIAL_DAYS` days tries nothing more, though no night ended it (the nightly learning switched off), and nor
    does one whose candidate was set aside or is gone: the night ends it."""
    trial: Trial | None = await trial_of(store)
    text, version_id = await adopted_text(store, held)
    if trial is None:
        return text, version_id, None
    if now - trial.started_at >= TRIAL_DAYS * DAY_S:
        log.info("harness_trial_past_its_days", version=trial.version_id)
        return text, version_id, None
    arm: Arm = arm_of(run_id)
    if arm == "in_use":
        return text, version_id, Seat(version=trial.version_id, arm=arm)
    try:
        candidate: HarnessVersion = await store.get_harness_version(trial.version_id)
    except NotFoundError:
        log.info("harness_trial_version_gone", version=trial.version_id, state="deleted")
        return text, version_id, None
    if candidate.state not in ("candidate", "probed"):
        log.info("harness_trial_version_gone", version=trial.version_id, state=candidate.state)
        return text, version_id, None
    return given_text(candidate, held), candidate.id, Seat(version=trial.version_id, arm=arm)
