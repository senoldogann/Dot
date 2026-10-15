"""The context size of a model's runs (item 250): how much of a run each step of the model sends before the older part
is summarised, where the owner chose one for the model instead of ``[agent] context_tokens``. ``[models.context]`` names
them, and Settings › Models sets them while the daemon runs, which the config file keeps too."""

from collections.abc import Mapping

from dotagent.core.logs import get_logger

log = get_logger(__name__)


class ContextSizes:
    """The context sizes chosen for models, as the config file named them when the runtime was built or as the owner
    chose them since (:meth:`choose`). One per runtime, read as each run starts, so that a choice holds from the next
    run on."""

    def __init__(self, sizes: Mapping[str, int]) -> None:
        self._sizes: dict[str, int] = dict(sizes)

    def all(self) -> dict[str, int]:
        """Every size chosen now, by model."""
        return dict(self._sizes)

    def of(self, model: str) -> int | None:
        """The size chosen for ``model`` (``provider:model``), ``None`` when its runs have ``[agent] context_tokens``."""
        return self._sizes.get(model)

    def choose(self, model: str, tokens: int | None) -> None:
        """Give the runs of ``model`` a context size of ``tokens`` from the next one on, or ``[agent] context_tokens``
        for ``None``; the runs under way keep theirs."""
        if tokens is None:
            self._sizes.pop(model, None)
        else:
            self._sizes[model] = tokens
        log.info("context_size_set", model=model, tokens=tokens)
