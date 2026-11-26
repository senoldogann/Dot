"""The cases of the memory benchmark (item 1728): owner facts saved over time, some later replaced, and a question each
whose right answer is the newest fact (LongMemEval's knowledge-update questions and LoCoMo's temporal ones, at the size
of Dot's own notes: 46 cases, one fresh store each).

A case's facts are saved in order by one run. ``answer`` is the fact the question wants (the newest of a fact that
changed; the first of two facts that are both true), ``outdated`` the facts it replaces, and ``used`` the facts that a
run was handed (so that a search lifts them) before the next one was saved. A fact with a ``subject`` is saved with the
key a run may give (``memory_save``'s ``subject``); one without is saved as a run saves a note when it names no slot.

The scripted embeddings take their numbers from these cases: ``pair_cosines`` are the cosines between the facts, in the
order (0, 1), (0, 2), (1, 2), and ``question_cosine`` the cosine of the question to each fact, as ``bge-m3`` measured on
these exact texts on 2026-11-24 (``tests/smoke/test_eval_memory_live.py`` measures them again). Updates of one value
lie between 0.80 and 0.97, two facts that are both true between 0.68 and 0.84, so the cosine alone does not tell which
is which; a note reworded into another sentence (U05, U06, U12) lies lower still.

The E cases (item 1866) are four of these cases again, with the same texts and cosines, whose old fact a second run was
handed before the newer one was saved and saves again afterwards (``echoed``): a schedule that started before the change
and writes back what recall gave it.
"""

from typing import Literal

from dotagent.core.types import FrozenModel


class Fact(FrozenModel):
    """A note a case saves: its text, the ``subject`` the run gave it (``None``: none) and how close in meaning the
    case's question is to it, for the scripted embeddings."""

    text: str
    subject: str | None
    question_cosine: float


class MemoryCase(FrozenModel):
    """Facts saved in order, a question, and which fact answers it (see the module's text)."""

    id: str
    kind: Literal["update", "addition"]
    facts: tuple[Fact, ...]
    pair_cosines: tuple[float, ...]
    question: str
    answer: int
    outdated: tuple[int, ...]
    used: tuple[int, ...]
    # The facts that a second run is handed as they are saved and saves again, word for word, once the newer note is
    # in (item 1866): recall's own text coming back as a new note. Empty for every case but the E cases.
    echoed: tuple[int, ...] = ()


_BASE: tuple[MemoryCase, ...] = (
    MemoryCase(
        id="K01",
        kind="update",
        facts=(
            Fact(text="The user lives in Oulu.", subject="user-city", question_cosine=0.716),
            Fact(text="The user lives in Helsinki.", subject="user-city", question_cosine=0.769),
        ),
        pair_cosines=(0.858,),
        question="Which city does the user live in?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K02",
        kind="update",
        facts=(
            Fact(text="The user prefers answers in English.", subject="reply-language", question_cosine=0.636),
            Fact(text="The user prefers answers in Turkish.", subject="reply-language", question_cosine=0.595),
        ),
        pair_cosines=(0.895,),
        question="In which language should Dot answer the user?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K03",
        kind="update",
        facts=(
            Fact(text="The daily standup is at 09:00.", subject="standup-time", question_cosine=0.796),
            Fact(text="The daily standup is at 10:30.", subject="standup-time", question_cosine=0.803),
        ),
        pair_cosines=(0.941,),
        question="When is the daily standup?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K04",
        kind="update",
        facts=(
            Fact(text="The user's hourly rate is 60 euros.", subject="hourly-rate", question_cosine=0.769),
            Fact(text="The user's hourly rate is 75 euros.", subject="hourly-rate", question_cosine=0.768),
        ),
        pair_cosines=(0.938,),
        question="What does the user charge per hour?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K05",
        kind="update",
        facts=(
            Fact(text="The user's main code editor is VS Code.", subject="code-editor", question_cosine=0.786),
            Fact(text="The user's main code editor is Zed.", subject="code-editor", question_cosine=0.787),
        ),
        pair_cosines=(0.853,),
        question="Which editor does the user code in?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K06",
        kind="update",
        facts=(
            Fact(text="Nordic Pines is waiting for a quote.", subject="nordic-pines-status", question_cosine=0.71),
            Fact(
                text="Nordic Pines accepted the quote and wants an invoice.",
                subject="nordic-pines-status",
                question_cosine=0.665,
            ),
        ),
        pair_cosines=(0.816,),
        question="What is the state of the Nordic Pines deal?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K07",
        kind="update",
        facts=(
            Fact(text="The user works on a MacBook Air M2.", subject="user-laptop", question_cosine=0.669),
            Fact(text="The user works on a MacBook Pro M4.", subject="user-laptop", question_cosine=0.685),
        ),
        pair_cosines=(0.896,),
        question="Which laptop does the user have?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K08",
        kind="update",
        facts=(
            Fact(text="Dot's daemon listens on port 8765.", subject="daemon-port", question_cosine=0.764),
            Fact(text="Dot's daemon listens on port 9120.", subject="daemon-port", question_cosine=0.76),
        ),
        pair_cosines=(0.887,),
        question="Which port does the daemon use?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K09",
        kind="update",
        facts=(
            Fact(
                text="The user's invoices are in the Finance folder.", subject="invoice-folder", question_cosine=0.771
            ),
            Fact(
                text="The user keeps invoices in the Billing folder.", subject="invoice-folder", question_cosine=0.755
            ),
        ),
        pair_cosines=(0.876,),
        question="Where are the invoices kept?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K10",
        kind="update",
        facts=(
            Fact(text="Releases go out on Fridays.", subject="release-day", question_cosine=0.821),
            Fact(text="Releases go out on Tuesdays.", subject="release-day", question_cosine=0.814),
        ),
        pair_cosines=(0.928,),
        question="On which day do releases go out?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K11",
        kind="update",
        facts=(
            Fact(text="Kullanıcı sabah 7'de kalkar.", subject="wake-time", question_cosine=0.895),
            Fact(text="Kullanıcı sabah 6'da kalkar.", subject="wake-time", question_cosine=0.893),
        ),
        pair_cosines=(0.952,),
        question="Kullanıcı sabah kaçta kalkar?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K12",
        kind="update",
        facts=(
            Fact(text="The user's dog is called Pixel.", subject="dog-name", question_cosine=0.818),
            Fact(text="The user's dog is called Kuzu.", subject="dog-name", question_cosine=0.797),
        ),
        pair_cosines=(0.804,),
        question="What is the user's dog called?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K13",
        kind="update",
        facts=(
            Fact(text="The user's invoice currency is EUR.", subject="invoice-currency", question_cosine=0.908),
            Fact(text="The user's invoice currency is USD.", subject="invoice-currency", question_cosine=0.92),
        ),
        pair_cosines=(0.958,),
        question="Which currency does the user invoice in?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="K14",
        kind="update",
        facts=(
            Fact(text="The default branch of the Dot repo is master.", subject="repo-branch", question_cosine=0.776),
            Fact(text="The default branch of the Dot repo is main.", subject="repo-branch", question_cosine=0.816),
        ),
        pair_cosines=(0.919,),
        question="What is the default branch of the repo?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U01",
        kind="update",
        facts=(
            Fact(text="The user's office is in Tampere.", subject=None, question_cosine=0.734),
            Fact(text="The user's office is in Turku.", subject=None, question_cosine=0.746),
        ),
        pair_cosines=(0.884,),
        question="Where is the user's office?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U02",
        kind="update",
        facts=(
            Fact(text="The team call is on Mondays.", subject=None, question_cosine=0.827),
            Fact(text="The team call is now on Wednesdays.", subject=None, question_cosine=0.803),
        ),
        pair_cosines=(0.897,),
        question="When is the team call?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U03",
        kind="update",
        facts=(
            Fact(text="The staging server has 4 CPU cores.", subject=None, question_cosine=0.893),
            Fact(text="The staging server has 8 CPU cores.", subject=None, question_cosine=0.894),
        ),
        pair_cosines=(0.935,),
        question="How many CPU cores does the staging server have?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U04",
        kind="update",
        facts=(
            Fact(text="The backup job runs at 02:00.", subject=None, question_cosine=0.802),
            Fact(text="The backup job runs at 03:30.", subject=None, question_cosine=0.797),
        ),
        pair_cosines=(0.951,),
        question="When does the backup job run?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U05",
        kind="update",
        facts=(
            Fact(text="The user lives in Espoo.", subject=None, question_cosine=0.737),
            Fact(
                text="The user moved to Vantaa in November, so Vantaa is their city now.",
                subject=None,
                question_cosine=0.676,
            ),
        ),
        pair_cosines=(0.678,),
        question="Which city does the user live in?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U06",
        kind="update",
        facts=(
            Fact(text="Blue Harbor has not replied to the proposal.", subject=None, question_cosine=0.837),
            Fact(text="Blue Harbor replied and asked for a lower price.", subject=None, question_cosine=0.755),
        ),
        pair_cosines=(0.696,),
        question="Has Blue Harbor replied to the proposal?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U07",
        kind="update",
        facts=(
            Fact(text="The user's favourite browser is Firefox.", subject=None, question_cosine=0.831),
            Fact(text="The user's favourite browser is Chrome.", subject=None, question_cosine=0.841),
        ),
        pair_cosines=(0.929,),
        question="What is the user's favourite browser?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U08",
        kind="update",
        facts=(
            Fact(text="The user drinks coffee in the morning.", subject=None, question_cosine=0.897),
            Fact(text="The user no longer drinks coffee in the morning.", subject=None, question_cosine=0.817),
        ),
        pair_cosines=(0.826,),
        question="Does the user drink coffee in the morning?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U09",
        kind="update",
        facts=(
            Fact(text="The monthly hosting budget is 40 euros.", subject=None, question_cosine=0.838),
            Fact(text="The monthly hosting budget is 55 euros.", subject=None, question_cosine=0.818),
        ),
        pair_cosines=(0.95,),
        question="What is the monthly hosting budget?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U10",
        kind="update",
        facts=(
            Fact(text="Kullanıcının ofisi Kadıköy'de.", subject=None, question_cosine=0.799),
            Fact(text="Kullanıcının ofisi Beşiktaş'ta.", subject=None, question_cosine=0.794),
        ),
        pair_cosines=(0.88,),
        question="Kullanıcının ofisi nerede?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U11",
        kind="update",
        facts=(
            Fact(text="The invoice template is invoice-v1.docx.", subject=None, question_cosine=0.752),
            Fact(text="The invoice template is invoice-v2.docx.", subject=None, question_cosine=0.768),
        ),
        pair_cosines=(0.972,),
        question="Which invoice template is used?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="U12",
        kind="update",
        facts=(
            Fact(text="The weekly report goes to Maria.", subject=None, question_cosine=0.753),
            Fact(
                text="Maria left the company, so the weekly report now goes to Jonas.",
                subject=None,
                question_cosine=0.647,
            ),
        ),
        pair_cosines=(0.817,),
        question="Who receives the weekly report?",
        answer=1,
        outdated=(0,),
        used=(),
    ),
    MemoryCase(
        id="C01",
        kind="update",
        facts=(
            Fact(text="The user lives in Oulu.", subject="user-home", question_cosine=0.716),
            Fact(text="The user lives in Helsinki.", subject="user-home", question_cosine=0.769),
            Fact(text="The user lives in Berlin.", subject="user-home", question_cosine=0.776),
        ),
        pair_cosines=(0.858, 0.77, 0.825),
        question="Which city does the user live in?",
        answer=2,
        outdated=(0, 1),
        used=(),
    ),
    MemoryCase(
        id="C02",
        kind="update",
        facts=(
            Fact(text="The release call is at 09:00.", subject="release-call", question_cosine=0.796),
            Fact(text="The release call is at 10:30.", subject="release-call", question_cosine=0.807),
            Fact(text="The release call is at 11:00.", subject="release-call", question_cosine=0.816),
        ),
        pair_cosines=(0.929, 0.928, 0.947),
        question="When is the release call?",
        answer=2,
        outdated=(0, 1),
        used=(),
    ),
    MemoryCase(
        id="C03",
        kind="update",
        facts=(
            Fact(text="The test database port is 5432.", subject=None, question_cosine=0.836),
            Fact(text="The test database port is 5433.", subject=None, question_cosine=0.848),
            Fact(text="The test database port is 5440.", subject=None, question_cosine=0.834),
        ),
        pair_cosines=(0.925, 0.953, 0.932),
        question="Which port does the test database use?",
        answer=2,
        outdated=(0, 1),
        used=(),
    ),
    MemoryCase(
        id="C04",
        kind="update",
        facts=(
            Fact(text="The user's terminal editor is Nano.", subject=None, question_cosine=0.797),
            Fact(text="The user's terminal editor is Vim.", subject=None, question_cosine=0.802),
            Fact(text="The user's terminal editor is Neovim.", subject=None, question_cosine=0.784),
        ),
        pair_cosines=(0.815, 0.839, 0.886),
        question="Which editor does the user use in the terminal?",
        answer=2,
        outdated=(0, 1),
        used=(),
    ),
    MemoryCase(
        id="A01",
        kind="addition",
        facts=(
            Fact(text="The user likes pizza.", subject=None, question_cosine=0.922),
            Fact(text="The user likes sushi.", subject=None, question_cosine=0.731),
        ),
        pair_cosines=(0.836,),
        question="Does the user like pizza?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A02",
        kind="addition",
        facts=(
            Fact(text="The user speaks Turkish.", subject="language-turkish", question_cosine=0.926),
            Fact(text="The user speaks Finnish.", subject="language-finnish", question_cosine=0.755),
        ),
        pair_cosines=(0.838,),
        question="Does the user speak Turkish?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A03",
        kind="addition",
        facts=(
            Fact(text="Meetings with Nordic Pines are on Tuesdays.", subject=None, question_cosine=0.881),
            Fact(text="Meetings with Blue Harbor are on Thursdays.", subject=None, question_cosine=0.585),
        ),
        pair_cosines=(0.719,),
        question="When are the meetings with Nordic Pines?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A04",
        kind="addition",
        facts=(
            Fact(text="The user uses Python for scripts.", subject=None, question_cosine=0.808),
            Fact(text="The user uses Rust for the desktop shell.", subject=None, question_cosine=0.623),
        ),
        pair_cosines=(0.725,),
        question="What does the user use for scripts?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A05",
        kind="addition",
        facts=(
            Fact(text="The user has a sister named Aylin.", subject=None, question_cosine=0.835),
            Fact(text="The user has a brother named Emre.", subject=None, question_cosine=0.754),
        ),
        pair_cosines=(0.789,),
        question="Who is the user's sister?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A06",
        kind="addition",
        facts=(
            Fact(text="The user's invoices are in the Finance folder.", subject=None, question_cosine=0.791),
            Fact(text="The user's receipts are scanned on Fridays.", subject=None, question_cosine=0.656),
        ),
        pair_cosines=(0.681,),
        question="Where are the invoices?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A07",
        kind="addition",
        facts=(
            Fact(text="Nordic Pines pays within 14 days.", subject=None, question_cosine=0.84),
            Fact(text="Blue Harbor pays within 30 days.", subject=None, question_cosine=0.564),
        ),
        pair_cosines=(0.704,),
        question="How fast does Nordic Pines pay?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="A08",
        kind="addition",
        facts=(
            Fact(text="The staging server has 4 CPU cores.", subject=None, question_cosine=0.893),
            Fact(text="The production server has 16 CPU cores.", subject=None, question_cosine=0.714),
        ),
        pair_cosines=(0.79,),
        question="How many CPU cores does the staging server have?",
        answer=0,
        outdated=(),
        used=(),
    ),
    MemoryCase(
        id="S01",
        kind="update",
        facts=(
            Fact(text="The user's office is in Lahti.", subject="office-city", question_cosine=0.766),
            Fact(text="The user's office is in Kuopio.", subject="office-city", question_cosine=0.744),
        ),
        pair_cosines=(0.841,),
        question="Where is the user's office?",
        answer=1,
        outdated=(0,),
        used=(0,),
    ),
    MemoryCase(
        id="S02",
        kind="update",
        facts=(
            Fact(text="The price of a website is 900 euros.", subject="website-price", question_cosine=0.843),
            Fact(text="The price of a website is 1200 euros.", subject="website-price", question_cosine=0.844),
        ),
        pair_cosines=(0.917,),
        question="What does a website cost?",
        answer=1,
        outdated=(0,),
        used=(0,),
    ),
    MemoryCase(
        id="S03",
        kind="update",
        facts=(
            Fact(text="The CI job timeout is 20 minutes.", subject=None, question_cosine=0.845),
            Fact(text="The CI job timeout is 45 minutes.", subject=None, question_cosine=0.853),
        ),
        pair_cosines=(0.944,),
        question="What is the CI job timeout?",
        answer=1,
        outdated=(0,),
        used=(0,),
    ),
    MemoryCase(
        id="S04",
        kind="update",
        facts=(
            Fact(text="The user's favourite music player is Spotify.", subject=None, question_cosine=0.856),
            Fact(text="The user's favourite music player is Apple Music.", subject=None, question_cosine=0.838),
        ),
        pair_cosines=(0.91,),
        question="What is the user's favourite music player?",
        answer=1,
        outdated=(0,),
        used=(0,),
    ),
)

_ECHOES: tuple[tuple[str, str], ...] = (("E01", "K01"), ("E02", "K03"), ("E03", "U03"), ("E04", "U09"))


def _echo_case(case_id: str, of: str) -> MemoryCase:
    """The case ``of`` again as ``case_id``, its old fact handed to a second run that saves it again at the end."""
    base: MemoryCase = next(case for case in _BASE if case.id == of)
    return base.model_copy(update={"id": case_id, "used": (), "echoed": base.outdated})


CASES: tuple[MemoryCase, ...] = (*_BASE, *(_echo_case(case_id, of) for case_id, of in _ECHOES))
