"""A start of a run that mixes a campaign with what it cannot take is refused, not half followed."""

import pytest
from pydantic import ValidationError

from dotagent.core.types import JsonObject
from dotagent.server.views import StartRunRequest


@pytest.mark.parametrize(
    "body",
    [
        {"goal": "Grow the list", "standing": True, "agent_id": "agt_1"},
        {"goal": "Grow the list", "browser": "attach"},
    ],
)
def test_a_campaign_choice_out_of_place_is_refused(body: JsonObject) -> None:
    with pytest.raises(ValidationError):
        StartRunRequest.model_validate(body)


def test_a_campaign_in_the_chrome_of_the_owner_and_an_agents_run_are_taken() -> None:
    campaign: StartRunRequest = StartRunRequest.model_validate({"goal": "Grow", "standing": True, "browser": "attach"})
    assert (campaign.standing, campaign.browser) == (True, "attach")
    assert StartRunRequest.model_validate({"goal": "Answer", "agent_id": "agt_1"}).agent_id == "agt_1"
