"""Where the plugin scout looks and the rules a plugin passes before it is read (item 710).

The catalogues are trimmed copies of Anthropic's own (2026-10-26); GitHub's answers are written here. Nothing reaches
the internet.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from dotagent.core.errors import GalleryError
from dotagent.plugins.scout_sources import (
    API,
    MIN_STARS_B,
    GitHub,
    Lead,
    OutOfRequests,
    RepoFacts,
    category_of,
    maker_lead,
    marketplace_leads,
    plugin_id,
    refusal,
    repo_facts,
    search_leads,
    unique,
)

FIXTURES: Path = Path(__file__).parent.parent / "fixtures" / "scout"
MARKET_COMMIT: str = "a" * 40
TODAY: date = date(2026, 10, 26)


def _catalogue(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_the_official_marketplace_gives_its_own_plugins_at_its_commit_and_others_at_the_commit_it_pins() -> None:
    leads: list[Lead] = marketplace_leads(
        _catalogue("official-marketplace.json"), "anthropics/claude-plugins-official", MARKET_COMMIT, "a", "official"
    )
    by_name: dict[str, Lead] = {lead.name: lead for lead in leads}
    own: Lead = by_name["agent-sdk-dev"]
    assert (own.repository, own.commit, own.path, own.publisher) == (
        "anthropics/claude-plugins-official",
        MARKET_COMMIT,
        "plugins/agent-sdk-dev",
        "Anthropic",
    )
    kept_elsewhere: Lead = by_name["42crunch-api-security-testing"]
    assert kept_elsewhere.repository == "42Crunch-AI/claude-plugins"
    assert kept_elsewhere.commit == "faf5305385de8afed9468904e8639be737aff39e"
    assert kept_elsewhere.path == "plugins/api-security-testing"
    assert all(lead.tier == "a" and lead.source == "official" for lead in leads)
    # A plugin kept elsewhere without a pinned commit, one off GitHub and one that leaves the repository are left out.
    assert {"unpinned", "escapes", "off-github"}.isdisjoint(by_name)


def test_a_catalogue_that_lists_its_skills_gives_them_and_one_that_lists_none_is_refused() -> None:
    leads: list[Lead] = marketplace_leads(
        _catalogue("skills-marketplace.json"), "anthropics/skills", MARKET_COMMIT, "a", "skills"
    )
    documents: Lead = next(lead for lead in leads if lead.name == "document-skills")
    assert documents.path == "" and documents.skills == ("skills/xlsx", "skills/docx", "skills/pptx", "skills/pdf")
    with pytest.raises(GalleryError, match="lists no plugins"):
        marketplace_leads({"name": "x"}, "a/b", MARKET_COMMIT, "a", "x")


def _facts(**changes: object) -> RepoFacts:
    facts: RepoFacts = RepoFacts(
        licence="MIT",
        stars=MIN_STARS_B,
        created=TODAY - timedelta(days=400),
        pushed=TODAY - timedelta(days=3),
        archived=False,
        owner="maker",
        default_branch="main",
    )
    return facts.model_copy(update=changes)


@pytest.mark.parametrize(
    ("changes", "tier", "refused"),
    [
        ({}, "b", None),
        ({"licence": "Apache-2.0"}, "b", None),
        ({"licence": "GPL-3.0"}, "a", "its licence is GPL-3.0, not MIT or Apache-2.0"),
        ({"licence": None}, "a", "its licence is none, not MIT or Apache-2.0"),
        ({"archived": True}, "a", "its repository is archived"),
        ({"created": TODAY - timedelta(days=90)}, "b", None),
        ({"created": TODAY - timedelta(days=89)}, "a", "its repository is younger than 90 days"),
        ({"pushed": TODAY - timedelta(days=365)}, "b", None),
        ({"pushed": TODAY - timedelta(days=366)}, "a", "its repository was not pushed to in 365 days"),
        ({"stars": MIN_STARS_B - 1}, "a", None),
        ({"stars": MIN_STARS_B - 1}, "b", f"its repository has {MIN_STARS_B - 1} stars, fewer than {MIN_STARS_B}"),
    ],
)
def test_the_rules_pass_one_and_refuse_its_twin(changes: dict[str, object], tier: str, refused: str | None) -> None:
    lead: Lead = maker_lead("maker/skills").model_copy(update={"tier": tier})
    assert refusal(lead, _facts(**changes), TODAY) == refused


def test_githubs_answers_are_read_and_a_garbled_one_is_an_error() -> None:
    facts: RepoFacts = repo_facts(
        {
            "license": {"spdx_id": "Apache-2.0"},
            "stargazers_count": 11219,
            "created_at": "2025-01-02T03:04:05Z",
            "pushed_at": "2026-10-20T00:00:00Z",
            "archived": False,
            "owner": {"login": "huggingface"},
            "default_branch": "main",
        }
    )
    assert (facts.licence, facts.stars, facts.created, facts.owner) == (
        "Apache-2.0",
        11219,
        date(2025, 1, 2),
        "huggingface",
    )
    with pytest.raises(GalleryError):
        repo_facts({"stargazers_count": "many"})
    found: list[Lead] = search_leads(
        {"items": [{"full_name": "someone/great-skills", "owner": {"login": "someone"}, "description": "Skills"}, 7]},
        "agent-skills",
    )
    assert [(lead.repository, lead.tier, lead.commit, lead.source) for lead in found] == [
        ("someone/great-skills", "b", None, "GitHub topic agent-skills")
    ]


def test_ids_categories_and_repeats() -> None:
    assert plugin_id("42Crunch API Security") == "p-42crunch-api-security"
    assert plugin_id("Frontend Design") == "frontend-design"
    # A candidate's id has two characters at least: a plugin named "R" failed each night (the eighth push's review).
    assert plugin_id("R") == "r-plugin" and plugin_id("") == "p-plugin"
    assert category_of("Productivity") == "business" and category_of("monitoring") == "development"
    assert category_of(None) == "development"
    a: Lead = maker_lead("maker/skills")
    b: Lead = a.model_copy(update={"tier": "b", "source": "GitHub topic agent-skills"})
    assert unique([b, a]) == [a]


def test_a_repository_a_catalogue_names_is_checked_before_it_goes_into_an_address() -> None:
    """The review of the second push: a catalogue's ``repo`` went into GitHub's addresses unchecked, and one the
    gallery's model refuses ended every night."""
    document: dict[str, object] = {
        "plugins": [
            {"name": "query", "source": {"source": "github", "repo": "owner/name?x", "sha": "a" * 40}},
            {"name": "fragment", "source": {"source": "github", "repo": "owner/name#x", "sha": "a" * 40}},
            {"name": "deeper", "source": {"source": "github", "repo": "owner/name/more", "sha": "a" * 40}},
            {"name": "fine", "source": {"source": "github", "repo": "owner/name", "sha": "a" * 40}},
        ]
    }
    leads: list[Lead] = marketplace_leads(document, "anthropics/x", MARKET_COMMIT, "b", "community")
    assert [(lead.name, lead.repository) for lead in leads] == [("fine", "owner/name")]
    assert search_leads({"items": [{"full_name": "a/b?c"}, {"full_name": "a/b"}]}, "t")[0].repository == "a/b"
    with pytest.raises(ValueError):
        maker_lead("owner/name?x")


async def test_github_says_how_many_requests_are_left_and_none_is_made_past_the_reserve() -> None:
    """The review of the eighth push: without a token GitHub answers 60 requests an hour, which a night with several
    plugins installed spent, and the rest of the night met 403s as if each plugin had failed."""
    left: list[int] = [5, 3, 2]
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if "/search/" in str(request.url):
            headers = {"x-ratelimit-remaining": "0", "x-ratelimit-resource": "search"}
            return httpx.Response(403, json={"message": "rate limit"}, headers=headers)
        return httpx.Response(200, json={"sha": "a" * 40}, headers={"x-ratelimit-remaining": str(left.pop(0))})

    github: GitHub = GitHub(None, httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    try:
        assert not github.exhausted
        for _ in range(3):
            await github.commit_of("maker/skills", "main")
        assert github.remaining == 2 and github.exhausted
        with pytest.raises(OutOfRequests):
            await github.commit_of("maker/skills", "main")
        assert len(asked) == 3
        # The search has a limit of its own: its running out is said, and the interface's count stays as it was.
        with pytest.raises(OutOfRequests):
            await github.search("agent-skills")
        assert github.remaining == 2 and asked[-1].startswith(f"{API}/search/")
    finally:
        await github.aclose()
