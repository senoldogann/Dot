"""The smoke tests that call a cloud model through the local Ollama server (item 632).

A model whose name ends in ``:cloud`` answers from Ollama's servers, off this Mac. CLAUDE.md: a test calls no model and
reaches nothing outside this Mac unless it is marked ``live``. These tests were skipped only where the server was not
there, so on a Mac that has it, the gate's offline selection (``-m "not live and ..."``) called the model. A test of the
local embedding model stays as it is: it never leaves the Mac.
"""

from collections.abc import Callable
from typing import TypeVar

import pytest

_Test = TypeVar("_Test", bound=Callable[..., object])


def live_with(present: bool, reason: str) -> Callable[[_Test], _Test]:
    """Mark a test ``live``, and skip it unless ``present`` (the server or the model is there); ``reason`` says why."""

    def mark(test: _Test) -> _Test:
        marked: _Test = pytest.mark.skipif(not present, reason=reason)(test)
        return pytest.mark.live(marked)

    return mark
