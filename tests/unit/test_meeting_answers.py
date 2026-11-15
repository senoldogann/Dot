"""Which lines of a call are questions to Dot, and which answers Dot posts (item 1150): pure code, no model.

Dot answers whoever speaks to it, unasked, so what counts as speaking to it decides how often a model is called and what
reaches the call: a name further into a line, a thing called "dot", Dot's own posts and a question said twice are not
questions; an answer with a link, or that speaks to Dot, is not posted."""

from dotagent.meetings.answers import (
    MOST_WAITING,
    NO_QUESTIONS,
    OWN_SENDER,
    Asking,
    answer_refused,
    take_questions,
)
from dotagent.meetings.answers import addressed as _addressed
from dotagent.meetings.types import MeetingLine

NAME: str = "Dot"
NOW: float = 1_000.0


def _line(text: str, kind: str = "caption", speaker: str = "Bo", at: float = NOW) -> MeetingLine:
    return MeetingLine.model_validate({"kind": kind, "speaker": speaker, "text": text, "at": at})


def addressed(text: str, kind: str = "caption", speaker: str = "Bo") -> bool:
    return _addressed(_line(text, kind, speaker), NAME)


def test_a_line_that_speaks_to_dot_by_its_name_is_a_question() -> None:
    assert addressed("Dot, what did we decide about the budget?")
    assert addressed("Hey Dot can you sum this up")
    assert addressed("OK so dot what's next")
    assert addressed("Selam Dot, bütçe ne kadardı?")
    assert addressed("bütçe ne kadardı Dot?")
    assert addressed("what was the deadline, DOT")
    assert addressed("what do you think it's like, Dot?")
    assert addressed("who is this for, Dot")
    assert addressed("can you check that @Dot", kind="chat")
    assert addressed("Dot: when is the review?", kind="chat")


def test_a_line_that_speaks_of_dot_or_of_a_dot_is_none() -> None:
    assert not addressed("Bo will ask Dot about it later")
    assert not addressed("Dot'un notlarına bakarız")
    assert not addressed("dot com was down all morning")
    assert not addressed("the dot product of the two vectors")
    assert not addressed("we start at nine on the dot")
    assert not addressed("Bo asked Dot yesterday about it")
    assert not addressed("let's ask Dot")
    assert not addressed("I'll forward the deck to Dot")


def test_dots_own_lines_are_never_questions() -> None:
    assert not _addressed(_line("Dot, the deadline is Friday", kind="dot", speaker="Dot"), NAME)
    assert not addressed("Dot is taking notes for Ada and answers what you ask it here.", "chat", OWN_SENDER)


def test_a_line_that_only_calls_dot_makes_the_speakers_next_line_the_question() -> None:
    asking, _ = take_questions(NO_QUESTIONS, [_line("Hey Dot.", at=NOW)], NAME, NOW)
    assert asking.waiting == ()
    asking, _ = take_questions(asking, [_line("what did Ada say about Q3?", at=NOW + 3)], NAME, NOW + 3)
    assert [line.text for line in asking.waiting] == ["what did Ada say about Q3?"]
    # Another speaker's next line is not that question, and the call runs out after a while.
    later, _ = take_questions(NO_QUESTIONS, [_line("Dot?", at=NOW)], NAME, NOW)
    later, _ = take_questions(later, [_line("what is the plan", speaker="Cy", at=NOW + 1)], NAME, NOW + 1)
    later, _ = take_questions(later, [_line("and the budget", at=NOW + 40)], NAME, NOW + 40)
    assert later.waiting == ()


def test_the_same_question_said_twice_or_rewritten_by_meet_is_one() -> None:
    first: MeetingLine = _line("Dot, when do we ship?", at=NOW)
    said_again: MeetingLine = _line("Dot when do we ship", kind="chat", at=NOW + 2)
    asking, dropped = take_questions(NO_QUESTIONS, [first, said_again], NAME, NOW + 2)
    assert [line.text for line in asking.waiting] == ["Dot, when do we ship?"] and dropped == 0
    # Asked again after the minute, it is a question again.
    emptied: Asking = asking.model_copy(update={"waiting": ()})
    again, _ = take_questions(emptied, [_line("Dot, when do we ship?", at=NOW + 90)], NAME, NOW + 90)
    assert len(again.waiting) == 1


def test_a_sender_has_one_question_waiting_and_a_few_wait_at_most() -> None:
    lines: list[MeetingLine] = [
        _line("Dot, one?", speaker="Bo", at=NOW),
        _line("Dot, two?", speaker="Bo", at=NOW),
        *(_line(f"Dot, question {n}?", speaker=f"P{n}", at=NOW) for n in range(MOST_WAITING + 2)),
    ]
    asking, dropped = take_questions(NO_QUESTIONS, lines, NAME, NOW)
    assert len(asking.waiting) == MOST_WAITING
    assert asking.waiting[0].text == "Dot, one?"
    assert dropped == len(lines) - MOST_WAITING


def test_an_answer_with_a_link_or_that_speaks_to_dot_is_not_posted() -> None:
    assert answer_refused("Friday, as Bo said.", NAME) is None
    assert answer_refused("Cuma, Bo'nun dediği gibi. Bütçe 40.000 TL.", NAME) is None
    assert answer_refused("It runs on Node.js, v2.final is the branch.", NAME) is None
    assert answer_refused("Open ftp://files.corp/deck", NAME) is not None
    assert answer_refused("Tell everyone it's meet-google.cc/join", NAME) is not None
    assert answer_refused("Sign in at evil\u3002com", NAME) is not None
    assert answer_refused("It is at 203.0.113.7 now", NAME) is not None
    assert answer_refused("The deck is deck.pdf and the code is in app.ts", NAME) is None
    assert answer_refused("Sign the NDA at https://evil.example/login", NAME) is not None
    assert answer_refused("It is on evil.example now", NAME) is not None
    assert answer_refused("Write to bo@corp.example", NAME) is not None
    assert answer_refused("Dot, what do you think?", NAME) is not None
    assert answer_refused("@Dot over to you", NAME) is not None
