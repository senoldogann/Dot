"""What a run is told of a step the owner said yes to with a note (item 1140): the step was not taken, so that the
note is read first. The gate's words (:func:`noted_text`) and a batched browser or desktop step's
(:func:`noted_in_batch_text`), whose result reads the page or the screen again and so lets the yes go."""


def noted_text(note: str) -> str:
    """What the run is told of a step the owner said yes to with a note (item 1140): the step was not taken, so that
    the note is read first; done again exactly so, it goes through once without asking, unless the run read content
    others write since (``RunGate.taint`` lets the yes go)."""
    return (
        f"the user said yes with a note, so this step was not taken yet; their note comes first: “{note}”. Do what the "
        "note says. If it still calls for this exact step, do it again exactly so: it goes through once without asking "
        "them again, unless you read outside content since, when they are asked once more. If the note says otherwise, "
        "do not do it"
    )


def noted_in_batch_text(note: str) -> str:
    """What the run is told of a step of a batch (a browser's or the desktop's) that the owner said yes to with a note
    (item 1140): the batch's own result is a page or a screen read again, which lets the yes go (the pre-push review),
    so doing the step again asks them once more."""
    return (
        f"the user said yes with a note, so this step was not taken yet; their note comes first: “{note}”. Do what the "
        "note says. If it still calls for this exact step, do it again exactly so: they are asked once more, as the "
        "page or the screen was read again since. If the note says otherwise, do not do it"
    )
