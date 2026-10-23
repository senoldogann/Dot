"""How two smoke modules are made, which no run of them shows until it goes wrong (item 558): a module of hosted tests
asks for no key as it is read, which every run does (the offline ones too, and a Keychain that does not answer made
that a collection error and stopped the run), and the tests that start a headless Chrome carry the ``browser`` mark, so
that ``-m "not browser"`` starts none."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from dotagent import secrets

_SMOKE: Path = Path(__file__).resolve().parents[1] / "smoke"


def _read(name: str) -> ModuleType:
    """The smoke module ``name`` as pytest reads it before it selects a test."""
    spec = importlib.util.spec_from_file_location(f"_smoke_probe_{name}", _SMOKE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["test_hosted_services", "test_search_anthropic"])
def test_a_module_of_hosted_tests_asks_for_no_key_as_it_is_read(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def asked(*arguments: object) -> str | None:
        raise AssertionError(f"{name} looked for a key as it was read: {arguments}")

    monkeypatch.setattr(secrets, "resolve_secret", asked)
    monkeypatch.setattr(secrets, "_from_keychain", asked)
    _read(name)


@pytest.mark.parametrize(
    "test",
    [
        "test_every_task_of_the_catalog_is_sound",
        "test_the_site_tasks_are_served_for_the_attempt_and_count_the_numbers_of_a_long_run",
    ],
)
def test_the_tests_that_play_a_task_in_chrome_are_marked_browser(test: str) -> None:
    marks: list[pytest.Mark] = getattr(_read("test_evals_runner"), test).pytestmark
    assert "browser" in {mark.name for mark in marks}
