"""What a note is (item 1975): its kind, told by fixed rules from its tags and words, and how a run's starting notes
are ranked by kind for where the run came from. Every function here is pure.

A note is one of six kinds: about the owner (``profile``), about another person (``people``), how something is done
(``workflow``), what must or must not be done, a preference or a decision (``rule``), what is so now, a project's state
or a measurement (``fact``), and what a run learned (``lesson``). The store gives a new note the kind the rules tell
(:func:`rule_kind`) as it writes it; a note the rules cannot tell has none until the fast model's pass tells it
(:mod:`dotagent.memory.kinds`), and a run may name the kind of a note it saves. ``kind_by`` records which of them did.
"""

from collections.abc import Sequence
from typing import Literal

from dotagent.core.text import fold_search
from dotagent.core.types import FrozenModel

type MemoryKind = Literal["profile", "people", "workflow", "rule", "fact", "lesson"]
# Who told a note's kind: the fixed rules, the fast model, the run that saved it, or the owner.
type KindBy = Literal["rule", "model", "run", "owner"]

MEMORY_KINDS: tuple[MemoryKind, ...] = ("profile", "people", "workflow", "rule", "fact", "lesson")
KIND_BYS: tuple[KindBy, ...] = ("rule", "model", "run", "owner")

# The tags the reflection and the lesson writers give (``dotagent.memory.service.JOURNAL_TAG`` and
# ``dotagent.agent.lessons.LESSON_TAG``), written out here because the store sits below those modules.
_LESSON_TAG: str = "lesson"
_JOURNAL_TAG: str = "journal"
# Tags that say a note's kind, folded (case, accents, the Turkish dotless i), in Turkish and English as the owner's
# notes are. A note's first rule that holds tells its kind, in the order of :func:`rule_kind`.
_RULE_TAGS: frozenset[str] = frozenset(
    {
        "rule",
        "rules",
        "kural",
        "kurali",
        "karar",
        "decision",
        "tercih",
        "preference",
        "preferences",
        "owner-preference",
        "calisma-tarzi",
        "calisma-yontemi",
        "uslup",
        "yazim",
        "tone",
        "dot-karakter",
        "reddetme",
        "approval",
        "onay",
        "sinir",
        "policy",
    }
)
_WORKFLOW_TAGS: frozenset[str] = frozenset(
    {
        "workflow",
        "how-to",
        "howto",
        "akis",
        "pipeline",
        "otomasyon",
        "automation",
        "workaround",
        "deploy",
        "kurulum",
        "setup",
        "recipe",
    }
)
# A site's notes (``dotagent.agent.site_knowledge``: ``site-<host>``) tell how a site is worked.
_SITE_TAG_PREFIX: str = "site-"
_PEOPLE_TAGS: frozenset[str] = frozenset(
    {"people", "person", "kisi", "kisiler", "contact", "contacts", "client", "musteri", "customer", "colleague"}
)
_PROFILE_TAGS: frozenset[str] = frozenset(
    {"profile", "profiles", "aboutme", "about-me", "owner", "kisisel", "personal", "cv", "career", "studies"}
)
_FACT_TAGS: frozenset[str] = frozenset(
    {"project", "proje", "status", "durum", "olcum", "measurement", "benchmark", "hesap", "account", "accounts"}
)
# Words in a note's text that say it is a rule or a decision, folded; matched as parts of the text.
_RULE_WORDS: tuple[str, ...] = (
    "kurali",
    "karari",
    "standing rule",
    "'s rule",
    "decided",
    "does not want",
    "doesn't want",
    "wants ",
    "istiyor",
    "istemiyor",
    "onayladi",
)
_WORKFLOW_WORDS: tuple[str, ...] = ("how to ", "the way to ", "calisan yolu", "nasil ", "akisi:")


def rule_kind(content: str, tags: Sequence[str]) -> MemoryKind | None:
    """The kind the fixed rules tell of a note with ``content`` and ``tags``, ``None`` when they cannot tell. In order:
    a lesson by its tag, the reflection's journal as a fact, then a kind its tags say (a rule, a workflow and a site's
    note, a person, the owner, a fact), then a rule or a workflow its words say."""
    folded: frozenset[str] = frozenset(fold_search(tag) for tag in tags)
    if _LESSON_TAG in folded:
        return "lesson"
    if _JOURNAL_TAG in folded:
        return "fact"
    if not _RULE_TAGS.isdisjoint(folded):
        return "rule"
    if not _WORKFLOW_TAGS.isdisjoint(folded) or any(tag.startswith(_SITE_TAG_PREFIX) for tag in folded):
        return "workflow"
    if not _PEOPLE_TAGS.isdisjoint(folded):
        return "people"
    if not _PROFILE_TAGS.isdisjoint(folded):
        return "profile"
    if not _FACT_TAGS.isdisjoint(folded):
        return "fact"
    text: str = fold_search(content)
    if any(word in text for word in _RULE_WORDS):
        return "rule"
    if any(word in text for word in _WORKFLOW_WORDS):
        return "workflow"
    return None


class KindPlan(FrozenModel):
    """How the notes of a run of some sources are ranked by kind (:func:`kind_plan`): ``order`` is the order of the
    kinds within each group of starting notes, and ``standing`` the kinds whose standing and owner-written notes a run
    is handed whatever its goal; a note of another kind comes when it fits the goal, as any note does."""

    name: str
    order: tuple[MemoryKind, ...]
    standing: frozenset[MemoryKind]


# A code review (``dotagent.server.REVIEW_SOURCE``): its notes are already the ones about code (item 1712); what was
# learned and how things are done come first.
REVIEW_PLAN: KindPlan = KindPlan(
    name="review",
    order=("lesson", "workflow", "rule", "fact", "profile", "people"),
    standing=frozenset(MEMORY_KINDS),
)
# A schedule (``dotagent.scheduler.SCHEDULE_SOURCE``): how its work is done and the owner's rules first.
SCHEDULE_PLAN: KindPlan = KindPlan(
    name="schedule",
    order=("workflow", "rule", "fact", "lesson", "profile", "people"),
    standing=frozenset(MEMORY_KINDS),
)
# A helper: a run another run gave work to (``dotagent.store.provenance.HELPER_RUNS``). The run that gave the work was
# handed the standing notes and says in its brief what of them matters, so none stands for the helper: each comes when
# it fits the helper's goal (on 2026-11-26 the owner's business and character notes were handed to every helper,
# code reviews among them).
HELPER_PLAN: KindPlan = KindPlan(
    name="helper",
    order=("rule", "workflow", "lesson", "fact", "profile", "people"),
    standing=frozenset(),
)
# The run sources as the modules that start them name them (``tests/unit/test_memory_kinds.py`` holds each one to its
# constant): the store sits below those modules.
_REVIEW_RUN: str = "review"
_SCHEDULE_RUN: str = "schedule"
_HELPER_RUNS: frozenset[str] = frozenset({"delegate", "team"})


def kind_plan(source: str) -> KindPlan | None:
    """How the starting notes of a run of ``source`` are ranked by kind; ``None`` for a run of any other source (the
    owner's chats, a ``dotx`` command, a watcher's, a reply's), which starts with the notes it started with before
    kinds: the owner talks to it, and every standing note stands."""
    if source == _REVIEW_RUN:
        return REVIEW_PLAN
    if source == _SCHEDULE_RUN:
        return SCHEDULE_PLAN
    if source in _HELPER_RUNS:
        return HELPER_PLAN
    return None


def kind_rank(kind: MemoryKind | None, plan: KindPlan) -> int:
    """Where a note of ``kind`` stands in ``plan``'s order; a note whose kind is not told yet comes after every kind."""
    return len(plan.order) if kind is None else plan.order.index(kind)
