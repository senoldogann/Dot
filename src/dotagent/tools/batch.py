"""A batch of steps over its limit (item 56): the first steps run and the result says so, rather than the whole call
being refused and the turn lost. Shared by the browser and the desktop tools."""


def first_steps[StepT](steps: list[StepT], limit: int) -> list[StepT]:
    """The steps of a batch that run: the first ``limit`` of them."""
    return steps[:limit]


def batch_cut_note(asked: int, limit: int) -> str:
    """The line a result opens with when the batch had more steps than run; empty when every step ran."""
    if asked <= limit:
        return ""
    return (
        f"The batch had {asked} steps; the first {limit} ran (the most a batch holds). Look at the result, then send "
        f"the remaining {asked - limit}."
    )
