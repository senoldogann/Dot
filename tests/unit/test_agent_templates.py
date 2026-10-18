"""Ready-made agents (item 315): the gallery shows a template per card, and a card becomes an agent in one click, so
what every card needs is checked here where a click cannot reach it: an id handed out once, words that say what the
agent is for and what it needs of the Mac, and, for the Reviewer, the conventions this repository reviews by, because
an agent started from it should not have to be told them again."""

import pytest

from dotagent.agent.templates import TEMPLATES, find_template
from dotagent.core.errors import NotFoundError


def test_every_template_is_found_under_an_id_of_its_own() -> None:
    ids: list[str] = [template.id for template in TEMPLATES]
    assert len(ids) == len(set(ids))
    for template in TEMPLATES:
        assert find_template(template.id) is template
        assert template.name
        assert template.role
        assert template.instructions
        assert template.needs


def test_a_template_that_is_not_there_is_a_not_found() -> None:
    with pytest.raises(NotFoundError):
        find_template("no-such-template")


def test_the_reviewer_carries_the_review_conventions_of_this_repository() -> None:
    """An agent started from the Reviewer reviews the way REVIEW.md asks, and changes nothing but its report."""
    reviewer = find_template("reviewer")
    assert reviewer.tools == (), "the reviewer runs git and the checks itself, so it may use every tool"
    for said in ("REVIEW.md", "file and line", "Important", "Nit", "Pre-existing", "reports/", "draft_message"):
        assert said in reviewer.instructions, said
