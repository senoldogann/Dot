"""What a message to a chat with several tasks says by its words alone (item 2012): its quote, a stop, ``/new``, the
task a quote names, and the question a reply ends with."""

from dotagent.agent.lanes import (
    SplitMessage,
    TaskWords,
    is_stop_only,
    new_task_words,
    prose_question,
    quoted_task,
    split_quote,
)


def test_a_quote_is_the_block_of_lines_the_pages_write_then_a_blank_line() -> None:
    assert split_quote("> Which e-mail address?\n> Say one.\n\nme@example.com") == SplitMessage(
        quote="Which e-mail address?\nSay one.", words="me@example.com"
    )
    # Words that merely start with ">" are words, as the pages read them.
    assert split_quote(">3 files left") == SplitMessage(quote=None, words=">3 files left")
    assert split_quote("> no blank line\nafter") == SplitMessage(quote=None, words="> no blank line\nafter")


def test_a_stop_is_a_stop_word_and_nothing_else() -> None:
    assert is_stop_only("dur")
    assert is_stop_only("  Stop! ")
    assert is_stop_only("DURDUR.")
    assert not is_stop_only("dur, önce formu bitir")
    assert not is_stop_only("stop the x.com task and fill the form")


def test_new_starts_a_task_with_its_words_only() -> None:
    assert new_task_words("/new find a dentist open on Saturday") == "find a dentist open on Saturday"
    assert new_task_words("/new\nline one\nline two") == "line one\nline two"
    assert new_task_words("/new") is None
    assert new_task_words("/newer things") is None
    assert new_task_words("please /new task") is None


def test_a_quote_names_the_one_task_whose_messages_hold_its_start() -> None:
    form: TaskWords = TaskWords(
        thread_id="thr_form", texts=("Fill the form", "Which e-mail address should I write? The answer goes there.")
    )
    x: TaskWords = TaskWords(thread_id="thr_x", texts=("Go to x.com and improve my account", "Which handle?"))
    assert quoted_task("Which e-mail address should I write? The answer…", (form, x)) == "thr_form"
    assert quoted_task("go to X.com   and improve", (form, x)) == "thr_x"
    # Too short to tell, found in both, or in none: no task.
    assert quoted_task("Which", (form, x)) is None
    assert quoted_task("Which handle? Which e-mail", (form, x)) is None
    both: TaskWords = TaskWords(thread_id="thr_other", texts=("Which e-mail address should I write? again",))
    assert quoted_task("Which e-mail address should I write?", (form, both)) is None


def test_a_reply_asks_when_it_ends_with_a_question() -> None:
    assert prose_question("I found the form.\nWhich e-mail address should I write?  ") is not None
    assert prose_question("Done. The form is sent.") is None
    long: str = "word " * 200 + "which one?"
    asked: str | None = prose_question(long)
    assert asked is not None and len(asked) == 400 and asked.endswith("which one?")
