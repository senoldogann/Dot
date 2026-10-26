"""Where the plugin scout looks (item 710), and the rules a plugin must pass before a line of it is read.

The sources, by how far Dot trusts them before reading anything:

- tier ``a``: Anthropic's own marketplaces (:data:`MARKETPLACES`), whose catalogue (``.claude-plugin/marketplace.json``)
  Anthropic curates and which pins the commit of each plugin kept elsewhere, and the repositories of the makers of
  what their skills are about (:data:`MAKERS`);
- tier ``b``: Anthropic's community marketplace, and repositories GitHub finds by the topics agent skills are published
  under (:data:`SEARCH_TOPICS`), with stars enough.

Never: marketplaces nobody reviews (ClawHub's audit of 2026 found 341 malicious skills of 2,857), and skills.sh, which
ranks by installs but has no interface to read.

Each source gives :class:`Lead` s: a plugin, the repository and the commit it is at, and the folder of it that holds its
skills. :func:`refusal` is the rules (an open licence, a repository old enough, kept up, starred enough for tier
``b``); a lead it refuses is not downloaded. GitHub is read with the token of Customize › Connectors when there is
one (5,000 requests an hour) and without one otherwise (60), so a night reads a bounded number of repositories.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

import httpx
from pydantic import Field, ValidationError

from dotagent.core.errors import GalleryError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.plugins.gallery import GalleryCategory
from dotagent.plugins.scout_types import Tier

log = get_logger(__name__)

API: str = "https://api.github.com"
RAW: str = "https://raw.githubusercontent.com/{repository}/{commit}/.claude-plugin/marketplace.json"
TIMEOUT_S: float = 30.0
# The requests of the hour a night leaves unspent: it ends before GitHub refuses one.
RESERVE: int = 2
CATALOGUE_LIMIT_BYTES: int = 4 * 2**20

# Anthropic's marketplaces: the repository, how far Dot trusts what it lists, and the words a candidate says it came
# from. The official one lists Anthropic's plugins and its partners'; the community one, plugins their authors sent in.
MARKETPLACES: tuple[tuple[str, Tier, str], ...] = (
    ("anthropics/claude-plugins-official", "a", "Anthropic's official marketplace"),
    ("anthropics/skills", "a", "Anthropic's skills"),
    ("anthropics/knowledge-work-plugins", "a", "Anthropic's knowledge-work plugins"),
    ("anthropics/claude-plugins-community", "b", "Anthropic's community marketplace"),
)
# Repositories of the makers of what their skills are about, read whole for skills (tier a): those the gallery already
# took from their makers.
MAKERS: tuple[str, ...] = (
    "apollographql/skills",
    "cloudflare/skills",
    "huggingface/skills",
    "mongodb/agent-skills",
    "redis/agent-skills",
    "supabase/agent-skills",
)
SEARCH_TOPICS: tuple[str, ...] = ("agent-skills", "claude-skills", "claude-code-skills", "claude-code-plugin")
SEARCH_PER_TOPIC: int = 15

LICENCES: frozenset[str] = frozenset({"MIT", "Apache-2.0"})
MIN_AGE_DAYS: int = 90
MAX_IDLE_DAYS: int = 365
MIN_STARS_B: int = 200

# What a marketplace's category means among the gallery's.
_CATEGORIES: Mapping[str, GalleryCategory] = {
    "design": "design",
    "productivity": "business",
    "automation": "business",
    "business": "business",
    "sales": "business",
    "marketing": "business",
    "finance": "business",
    "writing": "writing",
    "learning": "writing",
    "data": "data",
    "analytics": "data",
    "database": "data",
    "product": "product",
}
_GITHUB_URL: re.Pattern[str] = re.compile(r"^https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")
# A repository as GitHub names it, as the gallery takes it (``GalleryEntry.repository``): what a catalogue says is
# checked against it before it goes into an address.
REPOSITORY: re.Pattern[str] = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
_SHA: re.Pattern[str] = re.compile(r"^[0-9a-f]{40}$")
_NAME: re.Pattern[str] = re.compile(r"[^a-z0-9]+")


class Lead(FrozenModel):
    """A plugin a source names: ``path`` is the folder of ``repository`` its skills are under ("" for the whole
    repository), ``skills`` the skill folders the catalogue lists, if it lists them, and ``commit`` the commit the
    catalogue pins, None when the repository's newest is to be taken."""

    name: str
    publisher: str
    description: str
    category: GalleryCategory
    repository: str = Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    commit: str | None
    path: str
    skills: tuple[str, ...] = ()
    tier: Tier
    source: str


class RepoFacts(FrozenModel):
    """What GitHub says of a repository: its licence (SPDX), stars, when it was made and last pushed, whether it is
    archived, and its owner."""

    licence: str | None
    stars: int
    created: date
    pushed: date
    archived: bool
    owner: str
    default_branch: str


def plugin_id(name: str) -> str:
    """A gallery id made of a plugin's name: lower-case letters, digits and dashes, starting with a letter, two
    characters at least (a candidate's id; the review of the eighth push: a plugin named "R" failed each night)."""
    slug: str = _NAME.sub("-", name.lower()).strip("-")[:40].strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"p-{slug}"[:40].strip("-")
    return slug if len(slug) >= 2 else f"{slug}-plugin"


def category_of(raw: object) -> GalleryCategory:
    return _CATEGORIES.get(raw.lower(), "development") if isinstance(raw, str) else "development"


def _text(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _relative(path: str) -> str | None:
    """A folder of a repository as a catalogue names it (``./plugins/x``), without the dot; None for one that leaves
    the repository or names a hidden folder."""
    parts: list[str] = [part for part in path.strip().split("/") if part not in ("", ".")]
    if any(part == ".." or part.startswith(".") for part in parts):
        return None
    return "/".join(parts)


def _place(source: object, repository: str, commit: str) -> tuple[str, str, str] | None:
    """Where a catalogue's plugin is: its repository, the commit and the folder; None for one Dot does not take (off
    GitHub, not pinned to a commit, or out of its repository)."""
    if isinstance(source, str):
        relative: str | None = _relative(source)
        return None if relative is None else (repository, commit, relative)
    if not isinstance(source, Mapping) or source.get("source") not in ("url", "git-subdir", "github"):
        return None
    found: re.Match[str] | None = _GITHUB_URL.match(str(source.get("url") or ""))
    repo: str | None = found.group(1) if found else (str(source["repo"]) if source.get("repo") else None)
    sha: object = source.get("sha")
    relative = _relative(str(source.get("path") or ""))
    if repo is None or not REPOSITORY.match(repo) or not isinstance(sha, str) or not _SHA.match(sha):
        return None
    if relative is None:
        return None
    return repo, sha, relative


def _listed_skills(listed: object) -> tuple[str, ...]:
    if not isinstance(listed, list):
        return ()
    return tuple(folder for item in listed if isinstance(item, str) and (folder := _relative(item)))


def marketplace_leads(document: object, repository: str, commit: str, tier: Tier, label: str) -> list[Lead]:
    """The plugins a marketplace's catalogue lists, as leads: one kept in the marketplace's own repository at the
    marketplace's commit, one kept elsewhere on GitHub at the commit the catalogue pins (a plugin it does not pin, or
    keeps off GitHub, is left out: its commit could change under the review)."""
    plugins: object = document.get("plugins") if isinstance(document, Mapping) else None
    if not isinstance(plugins, list):
        raise GalleryError("the marketplace's catalogue lists no plugins", {"marketplace": repository})
    leads: list[Lead] = []
    for plugin in plugins:
        if not isinstance(plugin, Mapping):
            continue
        name: str = _text(plugin.get("name"), 40)
        place: tuple[str, str, str] | None = _place(plugin.get("source"), repository, commit)
        if not name or place is None:
            continue
        author: object = plugin.get("author")
        publisher: str = _text(author.get("name") if isinstance(author, Mapping) else None, 40)
        leads.append(
            Lead(
                name=name,
                publisher=publisher or repository.split("/")[0],
                description=_text(plugin.get("description"), 300),
                category=category_of(plugin.get("category")),
                repository=place[0],
                commit=place[1],
                path=place[2],
                skills=_listed_skills(plugin.get("skills")),
                tier=tier,
                source=label,
            )
        )
    return leads


def repo_facts(answer: object) -> RepoFacts:
    """GitHub's answer for a repository, as :class:`RepoFacts`. Raises GalleryError for one that is not."""
    try:
        if not isinstance(answer, Mapping):
            raise TypeError("not an object")
        licence: object = answer.get("license")
        owner: object = answer.get("owner")
        return RepoFacts(
            licence=str(licence["spdx_id"]) if isinstance(licence, Mapping) and licence.get("spdx_id") else None,
            stars=int(answer["stargazers_count"]),
            created=datetime.fromisoformat(str(answer["created_at"])).date(),
            pushed=datetime.fromisoformat(str(answer["pushed_at"])).date(),
            archived=bool(answer.get("archived")),
            owner=str(owner.get("login")) if isinstance(owner, Mapping) else "",
            default_branch=str(answer.get("default_branch") or "main"),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise GalleryError(
            "GitHub's answer for the repository could not be read", {"error": str(error)[:200]}
        ) from error


def refusal(lead: Lead, facts: RepoFacts, today: date) -> str | None:
    """Why ``lead`` is not looked at, or None when it passes the rules: an open licence (MIT or Apache-2.0), a
    repository at least :data:`MIN_AGE_DAYS` old (a skill published from a new account is the commonest attack), not
    archived, pushed within :data:`MAX_IDLE_DAYS`, and for tier ``b`` at least :data:`MIN_STARS_B` stars."""
    if facts.licence not in LICENCES:
        return f"its licence is {facts.licence or 'none'}, not MIT or Apache-2.0"
    if facts.archived:
        return "its repository is archived"
    if today - facts.created < timedelta(days=MIN_AGE_DAYS):
        return f"its repository is younger than {MIN_AGE_DAYS} days"
    if today - facts.pushed > timedelta(days=MAX_IDLE_DAYS):
        return f"its repository was not pushed to in {MAX_IDLE_DAYS} days"
    if lead.tier == "b" and facts.stars < MIN_STARS_B:
        return f"its repository has {facts.stars} stars, fewer than {MIN_STARS_B}"
    return None


def search_leads(answer: object, topic: str) -> list[Lead]:
    """The repositories GitHub's search found under ``topic``, as tier ``b`` leads of their newest commit."""
    items: object = answer.get("items") if isinstance(answer, Mapping) else None
    leads: list[Lead] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("full_name"), str):
            continue
        if not REPOSITORY.match(item["full_name"]):
            continue
        repository: str = item["full_name"]
        owner: object = item.get("owner")
        login: str = str(owner.get("login")) if isinstance(owner, Mapping) else repository.split("/")[0]
        leads.append(
            Lead(
                name=repository.split("/")[1][:40],
                publisher=login[:40],
                description=_text(item.get("description"), 300),
                category="development",
                repository=repository,
                commit=None,
                path="",
                tier="b",
                source=f"GitHub topic {topic}",
            )
        )
    return leads


def maker_lead(repository: str) -> Lead:
    """A maker's repository (:data:`MAKERS`) as a tier ``a`` lead of its newest commit."""
    return Lead(
        name=repository.split("/")[1][:40],
        publisher=repository.split("/")[0][:40],
        description="",
        category="development",
        repository=repository,
        commit=None,
        path="",
        tier="a",
        source="the maker's own repository",
    )


class OutOfRequests(GalleryError):
    """GitHub's requests of the hour ran out (60 without a token): the night ends there, and what it did not reach
    waits for the next one."""


class GitHub:
    """GitHub's REST interface, read with a token when there is one. Every request has :data:`TIMEOUT_S`; an answer
    that is not 200 is a GalleryError with its status, which the scout logs before it goes on with the next lead.

    The requests GitHub still allows this hour are read from each answer (``X-RateLimit-Remaining`` of its ``core``
    resource): once :data:`RESERVE` or fewer are left, a request to the interface is not made but is
    :class:`OutOfRequests`, and so is an answer that says they ran out (the search's own limit too)."""

    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None) -> None:
        headers: dict[str, str] = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers)
        self.requests: int = 0
        self.remaining: int | None = None

    @property
    def exhausted(self) -> bool:
        """Whether the interface's requests of the hour are spent, down to :data:`RESERVE`."""
        return self.remaining is not None and self.remaining <= RESERVE

    async def get_json(self, url: str) -> object:
        core: bool = url.startswith(API) and not url.startswith(f"{API}/search/")
        if core and self.exhausted:
            raise OutOfRequests("GitHub's requests of the hour ran out", {"remaining": self.remaining})
        self.requests += 1
        try:
            response: httpx.Response = await self._client.get(url)
        except httpx.HTTPError as error:
            raise GalleryError(f"GitHub could not be reached ({type(error).__name__})", {"url": url}) from error
        left: str | None = response.headers.get("x-ratelimit-remaining")
        if left is not None and left.isdigit():
            if response.headers.get("x-ratelimit-resource", "core") == "core":
                self.remaining = int(left)
            if int(left) == 0 and response.status_code in (httpx.codes.FORBIDDEN, httpx.codes.TOO_MANY_REQUESTS):
                raise OutOfRequests(f"GitHub answered {response.status_code}: its requests ran out", {"url": url})
        if response.status_code != httpx.codes.OK:
            raise GalleryError(f"GitHub answered {response.status_code}", {"url": url})
        if len(response.content) > CATALOGUE_LIMIT_BYTES:
            raise GalleryError("GitHub's answer is too large", {"url": url})
        try:
            return response.json()
        except ValueError as error:
            raise GalleryError("GitHub's answer is not JSON", {"url": url}) from error

    async def repo(self, repository: str) -> RepoFacts:
        return repo_facts(await self.get_json(f"{API}/repos/{repository}"))

    async def commit_of(self, repository: str, ref: str) -> str:
        answer: object = await self.get_json(f"{API}/repos/{repository}/commits/{ref}")
        sha: object = answer.get("sha") if isinstance(answer, Mapping) else None
        if not isinstance(sha, str) or not _SHA.match(sha):
            raise GalleryError("GitHub named no commit", {"repository": repository, "ref": ref})
        return sha

    async def marketplace(self, repository: str, commit: str) -> object:
        return await self.get_json(RAW.format(repository=repository, commit=commit))

    async def search(self, topic: str) -> object:
        query: str = f"topic:{topic}+stars:>={MIN_STARS_B}+archived:false"
        return await self.get_json(
            f"{API}/search/repositories?q={query}&sort=stars&order=desc&per_page={SEARCH_PER_TOPIC}"
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def unique(leads: Sequence[Lead]) -> list[Lead]:
    """``leads`` without repeats of one plugin (the same repository and folder), the most trusted kept."""
    kept: dict[tuple[str, str], Lead] = {}
    for lead in sorted(leads, key=lambda one: one.tier):
        kept.setdefault((lead.repository.lower(), lead.path), lead)
    return list(kept.values())
