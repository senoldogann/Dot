"""Which stored tool results are failed calls (item 1722): a ``tool_result`` event whose ``ok`` is false and that is not
``steered``, a call Dot's own rule turned another way (:class:`dotagent.core.errors.ToolSteer`). ``ok`` says whether
the call did its job, which a steered call did not; the counts of failures leave it out."""


def failed_result_sql(data: str) -> str:
    """The SQL condition that the event JSON in the column ``data`` is a failed call."""
    return f"(json_extract({data}, '$.ok') = 0 AND COALESCE(json_extract({data}, '$.steered'), 0) = 0)"
