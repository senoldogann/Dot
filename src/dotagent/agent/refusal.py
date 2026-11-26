"""A model's refusal of its task, and what Dot does about it (item 1889, docs/models.md).

A model that declines in-scope work may lack what the owner knows: that the target is in a bug bounty program's scope,
or that the task is the owner's own words. Dot tells it that once, openly, as a note of its own, and asks the same
model again; a second no stands. Dot never argues past a refusal, never words the model's answer for it, and never
moves the run to another model on its own: the run ends with the model's words, and the owner picks the model to
restart on.

Everything here is pure: the loop reads the scopes (:func:`dotagent.scopes.authorising`) and passes the lookup in.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from dotagent.config import ReconAuthority

# The phrasings a refusal of the task opens with, in English and Turkish. Each says no to the request itself ("I can't
# help with that", "bu konuda yardımcı olamam"), not to a means ("I can't reach the host without a key"), so that a
# final answer that names a limit it met is read as work.
_REFUSALS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:sorry|afraid|apologi[sz]e)\b[^.!?\n]{0,30}\bbut i (?:can(?:'|’|no)t|cannot|won(?:'|’)t|will not|"
        r"am unable to|am not able to) (?:help|assist|comply|fulfil+|(?:do|provide|write|share) (?:that|this|it)\b)"
        r"(?![^.!?\n]{0,40}\b(?:without|until|unless|before|from here)\b)",
        r"\bi (?:can(?:'|’|no)t|cannot|won(?:'|’)t|will not|am unable to|am not able to|(?:'|’)m not able to|"
        r"(?:'|’)m unable to) (?:help|assist) (?:you )?with (?:that|this|it|requests?|your request)\b"
        r"(?![^.!?\n]{0,40}\b(?:without|until|unless|before|from here)\b)",
        r"\bi (?:must|have to|need to) (?:respectfully )?(?:decline|refuse)\b",
        r"\bi (?:can(?:'|’|no)t|cannot|won(?:'|’)t|will not) do (?:that|this|it)\s*[.!]",
        r"\bi (?:can(?:'|’|no)t|cannot|won(?:'|’)t|will not) (?:comply with|fulfil+) (?:that|this|your)\b",
        r"\bi(?:'m|’m| am) not (?:going|willing) to (?:help|assist|provide|write|do) (?:that|this|it|with)\b",
        r"\b(?:that|this|it|your request) (?:would )?(?:goes? against|go against|violates?|violate|breaks?|break) my "
        r"(?:guidelines|usage polic(?:y|ies)|content polic(?:y|ies))\b",
        # The no is said to the request itself: help refused on it, or the request (bunu, bu isteği) not done. "Maalesef
        # VPN olmadan taramayı yapamam; pasif denetimi yaptım" names a limit it met, and is work.
        r"\b(?:bu|şu) (?:konuda|isteğe|talebe|isteğinizde|talebinizde)(?: (?:sana|size))? "
        r"(?:yardımcı olamam|yardımcı olamayacağım|yardımcı olamıyorum|destek olamam|destek veremem)",
        r"\b(?:bu|şu) (?:isteği|talebi|isteğinizi|talebinizi|isteğini|talebini) "
        r"(?:yerine getiremem|yerine getiremeyeceğim|karşılayamam)",
        r"\b(?:maalesef|üzgünüm|ne yazık ki),? (?:(?:bu|şu) (?:konuda|isteğe|talebe) )?(?:(?:sana|size) )?"
        r"(?:yardımcı olamam|yardımcı olamıyorum|yardımcı olamayacağım|destek olamam)",
        r"\b(?:maalesef|üzgünüm|ne yazık ki),? (?:bunu|(?:bu|şu) (?:isteği|talebi|isteğini|talebini)) "
        r"(?:yapamam|yerine getiremem|yapmayacağım)",
        r"\bbunu (?:yapamam|yapmayacağım|yerine getiremem|yazamam|yazmayacağım)\b",
        r"\b(?:reddetmek|geri çevirmek) zorundayım\b",
    )
)

# How much of a reply a refusal has to stand in: a refusal opens the answer, it is not a remark about one later.
_OPENING_CHARS: int = 400

# A host name in a goal: labels and a dot-separated top-level name, after a scheme or not. Only the hosts a scope or the
# owner's list covers count, so a file name that looks like one ("report.md") counts for nothing.
_HOST: re.Pattern[str] = re.compile(
    r"(?<![\w@.-])(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})\b", re.IGNORECASE
)

# The words a goal of security work holds, for the kind a run is routed by (`[models] kinds`).
_SECURITY_WORDS: re.Pattern[str] = re.compile(
    r"\b(?:security|vuln\w*|pentest\w*|penetration|bug ?bount\w*|recon|reconnaissance|scan|scans|"
    r"scanning|xss|sqli|sql injection|idor|ssrf|"
    r"csrf|rce|exploit\w*|cve|disclos\w*|güvenlik\w*|zafiyet\w*|açığ\w*|açıkları\w*|sızma\w*|"
    r"taraması\w*|tarama|taramak|tarayıp|tara)\b",
    re.IGNORECASE,
)

# The most of the owner's words the note quotes.
_WORDS_CHARS: int = 600

type Authority = Callable[[str], ReconAuthority | None]


@dataclass(frozen=True)
class ScopedHost:
    """A host a goal names and who allows a test of it: ``program`` ``None`` for the owner's own list."""

    host: str
    program: str | None
    note: str


@dataclass(frozen=True)
class RefusalGrounds:
    """What makes a declined task in-scope work: the hosts it names that a scope covers, and the owner's own words when
    the goal is theirs (``""`` when it is not)."""

    hosts: tuple[ScopedHost, ...]
    owner_words: str


def looks_like_refusal(text: str) -> bool:
    """Whether ``text`` opens by declining the task, rather than with the work or with a limit it met."""
    opening: str = text.strip()[:_OPENING_CHARS]
    return any(pattern.search(opening) is not None for pattern in _REFUSALS)


def hosts_named(text: str) -> tuple[str, ...]:
    """The host names ``text`` holds, lower case, each once, in order."""
    return tuple(dict.fromkeys(match.group(1).lower() for match in _HOST.finditer(text)))


def scoped_hosts(goal: str, authority: Authority) -> tuple[ScopedHost, ...]:
    """The hosts ``goal`` names that ``authority`` (a scope lookup) covers, with who allows each."""
    found: list[ScopedHost] = []
    for host in hosts_named(goal):
        allowed: ReconAuthority | None = authority(host)
        if allowed is not None:
            found.append(ScopedHost(host=host, program=allowed.program, note=allowed.note))
    return tuple(found)


def refusal_grounds(goal: str, owners_words: bool, authority: Authority) -> RefusalGrounds | None:
    """The grounds a declined ``goal`` is asked again on: the scoped hosts it names, and the goal itself when it is the
    owner's own words (``owners_words``). ``None`` when it has neither: the refusal stands at once."""
    hosts: tuple[ScopedHost, ...] = scoped_hosts(goal, authority)
    words: str = goal.strip() if owners_words else ""
    if not hosts and not words:
        return None
    return RefusalGrounds(hosts=hosts, owner_words=words)


def _host_line(scoped: ScopedHost) -> str:
    if scoped.program is None:
        return f"- {scoped.host} is on the owner's own list of hosts, theirs to test."
    # The program's name and rules may be the words of its page, copied by a run that registered the scope: they are
    # quoted as recorded, on one line, never as Dot's own words.
    program: str = _one_line(scoped.program)
    rules: str = f' Its rules, as recorded in the scope: "{_one_line(scoped.note)}"' if scoped.note else ""
    return f'- {scoped.host} is in the scope "{program}" the owner recorded (`dotx scope list`).{rules}'


def _one_line(text: str) -> str:
    """``text`` on one line, its quotes made single, so that it stays inside the quotes it is set in."""
    return " ".join(text.split()).replace('"', "'")


def grounds_note(grounds: RefusalGrounds) -> str:
    """The note that gives a model that declined its task the context the task came with, once and openly."""
    lines: list[str] = [_host_line(scoped) for scoped in grounds.hosts]
    if grounds.owner_words:
        words: str = grounds.owner_words
        quoted: str = words if len(words) <= _WORDS_CHARS else f"{words[:_WORDS_CHARS]}…"
        lines.append(f'- The task is the owner\'s own words, sent by them: "{quoted}"')
    listed: str = "\n".join(lines)
    return (
        "[system note] Your last reply declined the task. Here, once, is the context it comes with:\n"
        f"{listed}\n"
        "Security work here is finding flaws and reporting them, with the evidence, in the form the program asks for; "
        "it is not exploiting them past what proves a finding, nor harming the systems or their users. Read the task "
        "again with this. If you still decline, say so plainly with your reason: the run ends there with your words, "
        "and the owner decides what comes next."
    )


def security_work(goal: str, authority: Authority) -> bool:
    """Whether ``goal`` is security research within a scope: it names a host a scope or the owner's list covers, and
    speaks of security work. The kind ``security`` of ``[models] kinds``."""
    return _SECURITY_WORDS.search(goal) is not None and bool(scoped_hosts(goal, authority))


# The run asked its model once more with the grounds of its task (``model``, ``hosts``, ``owner_words``).
REFUSAL_ASKED_AGAIN_EVENT: str = "refusal_asked_again"
# The run ended on its model's refusal (``model``, ``words``): its page offers a restart on a model the owner picks.
MODEL_REFUSED_EVENT: str = "model_refused"
# The owner restarted the run's task on a model they picked (``model``, ``run_id`` of the new run, ``chosen_by``).
RESTARTED_ON_MODEL_EVENT: str = "restarted_on_model"
