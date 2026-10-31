"""What the owner's own record showed models trip on (item 879): an edit sent with another tool's names for its
texts, a wait or a count past its bound, and an app's server that failed answered as Dot's own fault."""

import pytest
from pydantic import ValidationError

from dotagent.core.errors import McpError, PluginError, SkillError
from dotagent.server.app import status_for
from dotagent.tools.browser import WaitTextArgs
from dotagent.tools.files import EditFileArgs
from dotagent.tools.memory import SearchArgs
from dotagent.tools.schema import tool_parameters_schema
from dotagent.tools.terminal import ReadArgs


@pytest.mark.parametrize(("old", "new"), [("old", "new"), ("old_text", "new_text"), ("old_string", "new_string")])
def test_an_edit_reads_the_names_other_edit_tools_give_its_texts(old: str, new: str) -> None:
    args: EditFileArgs = EditFileArgs.model_validate({"path": "a.txt", old: "before", new: "after"})
    assert (args.old, args.new) == ("before", "after")


def test_an_edit_that_names_one_text_twice_is_refused_and_the_model_is_shown_old_and_new() -> None:
    with pytest.raises(ValidationError):
        EditFileArgs.model_validate({"path": "a.txt", "old": "x", "new": "y", "new_text": "z"})
    assert set(tool_parameters_schema(EditFileArgs)["properties"]) == {"path", "old", "new", "replace_all"}  # type: ignore[arg-type]


def test_a_wait_or_a_count_past_its_bound_is_read_as_the_bound() -> None:
    assert ReadArgs.model_validate({"session": "t1", "wait_s": 120}).wait_s == 60
    assert SearchArgs.model_validate({"query": "thesis", "limit": 50}).limit == 20
    assert WaitTextArgs.model_validate({"text": "Done", "timeout_s": 90}).timeout_s == 60
    assert WaitTextArgs.model_validate({"text": "Done"}).timeout_s is None
    # Below its floor it is still refused: the cap is for a call made too long, not for one that makes no sense.
    with pytest.raises(ValidationError):
        ReadArgs.model_validate({"session": "t1", "wait_s": -1})


@pytest.mark.parametrize("error", [McpError("the server ended", {}), SkillError("no skill", {}), PluginError("no", {})])
def test_an_apps_server_or_a_file_that_failed_is_the_owners_to_read_not_a_fault_of_dots(error: Exception) -> None:
    assert status_for(error) == 422  # type: ignore[arg-type]
