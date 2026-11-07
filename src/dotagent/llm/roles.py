"""The models of Dot's own work (item 232): the fast model, which writes the summaries of long runs first and reads
pages for the facts asked of them, the review model, which checks runs and draws their lessons, and the vision model,
which reads images for a model that cannot; and the standby models a run moves to when its own model cannot answer
(item 478). ``[models]`` names them, and Settings › Models chooses them while the daemon runs, which the config file
keeps too; so does it the model that looks for work on Dot's own initiative (``[initiative] look_model``, items 910 and
916), "" for the default model."""

from collections.abc import Mapping, Sequence
from typing import Literal

from dotagent.config import HarnessConfig, ModelReference, ModelsConfig
from dotagent.core.logs import get_logger

log = get_logger(__name__)

type ModelRole = Literal["fast", "review", "vision"]
# The roles of the improvement loop (item 390, ``[harness]``): each a single question, on the review model unless one is
# named for it.
type LearningRole = Literal["proposer", "composer", "selector", "experience"]
LEARNING_ROLES: tuple[LearningRole, ...] = ("proposer", "composer", "selector", "experience")


def learning_of(harness: HarnessConfig) -> dict[LearningRole, str]:
    """The model ``[harness]`` names for each role of the improvement loop, "" for the review model."""
    return {
        "proposer": harness.proposer,
        "composer": harness.composer,
        "selector": harness.selector,
        "experience": harness.experience,
    }


class ModelRoles:
    """The models of Dot's own work, as the config file named them when the runtime was built or as the owner chose them
    since (:meth:`choose`). One per runtime, read as each call is made, so that a choice holds from the next call on."""

    def __init__(self, models: ModelsConfig, look: str, learning: Mapping[LearningRole, str]) -> None:
        self._models: dict[ModelRole, ModelReference] = {
            "fast": models.fast,
            "review": models.review,
            "vision": models.vision,
        }
        self._standby: tuple[ModelReference, ...] = tuple(models.standby)
        self._look: str = look
        self._learning: dict[LearningRole, str] = {role: learning[role] for role in LEARNING_ROLES}

    def fast(self) -> ModelReference:
        return self._models["fast"]

    def review(self) -> ModelReference:
        return self._models["review"]

    def vision(self) -> ModelReference:
        return self._models["vision"]

    def standby(self) -> tuple[ModelReference, ...]:
        """The models a run moves to, in their order, when its own model cannot answer (``[models] standby``)."""
        return self._standby

    def look(self) -> str:
        """The model of the run that looks for work on Dot's own initiative (``[initiative] look_model``); "" is the
        default model."""
        return self._look

    def choose_look(self, model: str) -> None:
        """Have ``model`` ("" for the default one) look for work from the next look on (item 916); one under way keeps
        its own."""
        self._look = model
        log.info("look_model_set", model=model)

    def learning(self, role: LearningRole) -> str:
        """The model named for ``role`` of the improvement loop (``[harness] <role>``), "" when it plays on the review
        model."""
        return self._learning[role]

    def learning_model(self, role: LearningRole) -> ModelReference:
        """The model that plays ``role`` of the improvement loop: the one named for it, else the review model."""
        named: str = self._learning[role]
        return named if named != "" else self.review()

    def choose_learning(self, role: LearningRole, model: str) -> None:
        """Have ``model`` ("" for the review model) play ``role`` of the improvement loop from its next question on
        (item 1033); a question under way keeps its own."""
        self._learning[role] = model
        log.info("learning_model_set", role=role, model=model)

    def after_review(self, own: Sequence[ModelReference] = ()) -> tuple[ModelReference, ...]:
        """The models a question of Dot's own learning goes to, in turn, when the review model declines it or cannot
        answer (item 649): ``own``, the model of the run it is about, then the standby models; each once."""
        return tuple(dict.fromkeys((*own, *self._standby)))

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
