"""The models of Dot's own work (item 232): the fast model, which writes the summaries of long runs first and reads pages
for the facts asked of them, the review model, which checks runs and draws their lessons, and the vision model, which
reads images for a model that cannot. ``[models]`` names them, and Settings › Models chooses them while the daemon runs,
which the config file keeps too."""

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

    def fast(self) -> ModelReference:
        return self._models["fast"]

    def review(self) -> ModelReference:
        return self._models["review"]

    def vision(self) -> ModelReference:
        return self._models["vision"]

    def choose(self, role: ModelRole, model: ModelReference) -> None:
        """Have ``model`` do the work of ``role`` from the next call on; the calls under way keep theirs."""
        self._models[role] = model
        log.info("role_model_set", role=role, model=model)

    def applied(self, models: ModelsConfig) -> ModelsConfig:
        """``models`` with the models of Dot's own work as they are now."""
        return models.model_copy(update={"fast": self.fast(), "review": self.review(), "vision": self.vision()})
