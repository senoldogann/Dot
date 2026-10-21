"""The models of Dot's own work (item 232): the fast model, which writes the summaries of long runs first and reads
pages for the facts asked of them, the review model, which checks runs and draws their lessons, and the vision model,
which reads images for a model that cannot; and the standby models a run moves to when its own model cannot answer
(item 478). ``[models]`` names them, and Settings › Models chooses them while the daemon runs, which the config file
keeps too."""

from collections.abc import Sequence
from typing import Literal

from dotagent.config import ModelReference, ModelsConfig
from dotagent.core.logs import get_logger

log = get_logger(__name__)

type ModelRole = Literal["fast", "review", "vision"]


class ModelRoles:
    """The models of Dot's own work, as the config file named them when the runtime was built or as the owner chose them
    since (:meth:`choose`). One per runtime, read as each call is made, so that a choice holds from the next call on."""

    def __init__(self, models: ModelsConfig) -> None:
        self._models: dict[ModelRole, ModelReference] = {
            "fast": models.fast,
            "review": models.review,
            "vision": models.vision,
        }
        self._standby: tuple[ModelReference, ...] = tuple(models.standby)

    def fast(self) -> ModelReference:
        return self._models["fast"]

    def review(self) -> ModelReference:
        return self._models["review"]

    def vision(self) -> ModelReference:
        return self._models["vision"]

    def standby(self) -> tuple[ModelReference, ...]:
        """The models a run moves to, in their order, when its own model cannot answer (``[models] standby``)."""
        return self._standby

    def choose(self, role: ModelRole, model: ModelReference) -> None:
        """Have ``model`` do the work of ``role`` from the next call on; the calls under way keep theirs."""
        self._models[role] = model
        log.info("role_model_set", role=role, model=model)

    def choose_standby(self, models: Sequence[ModelReference]) -> None:
        """Have a run whose own model cannot answer move to ``models``, in their order, from the next such move on (item
        478); none ends such a run with its model's error. A run that moved already keeps the model it moved to."""
        self._standby = tuple(models)
        log.info("standby_models_set", models=list(models))

    def applied(self, models: ModelsConfig) -> ModelsConfig:
        """``models`` with the models of Dot's own work and the standby models as they are now."""
        return models.model_copy(
            update={
                "fast": self.fast(),
                "review": self.review(),
                "vision": self.vision(),
                "standby": list(self._standby),
            }
        )
