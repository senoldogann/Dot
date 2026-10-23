"""What every test shares: the keys of the owner stay out of an offline test (item 558).

A test calls no model and reaches nothing outside this Mac, except those marked ``hosted``, ``internet`` and ``live``.
The keys of the providers are found in the environment, in the ``.env`` file of the home and in the owner's login
Keychain (:func:`dotagent.secrets.resolve_secret`), and the home of a test is a throwaway folder while the first two of
those, and the Keychain, are the owner's: a test that listed the models of every provider, or asked the doctor, sent
the owner's real keys to OpenAI, OpenRouter and OpenCode Go, and one that asked what a key was found to be failed on
the owner's Mac alone. So, in a run where no test needs a key, the owner's values of the variables of the keys are out
of the environment and the Keychain is never asked, from the start (the fixtures of a module build a runtime that looks
for them); in a run that holds tests which do, each of the others goes without them while it runs. The tests that do
need them are marked, and find them as they run (``tests/smoke/test_hosted_services.py``).

Only the owner's values go: a test that sets a key of its own under the name of a provider's (a stand-in of the
provider, a key that is no key) keeps it. A child process that a test starts has the environment of this one, and asks
the Keychain for a key it needs by its own: a test that starts one gives it a key variable no source has, or a key of
its own."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from dotagent import secrets
from dotagent.config import load_default_config, providers_by_key

# The marks of the tests that send requests with the owner's keys or reach the world.
_NEEDS_KEYS: tuple[str, ...] = ("hosted", "internet", "live")


def _key_variables() -> tuple[str, ...]:
    """The variables the providers' keys are looked for in, as the defaults name them."""
    config = load_default_config()
    providers = config.providers
    return (*providers_by_key(config), providers.openai.organization_env, providers.openai.project_env)


# What the owner has in those variables, as the run began (read as this file is, before any fixture).
_OWNERS: dict[str, str] = {name: os.environ[name] for name in _key_variables() if name in os.environ}


def _needs_keys(item: pytest.Item) -> bool:
    return any(item.get_closest_marker(name) is not None for name in _NEEDS_KEYS)


@contextmanager
def _without_the_owners_keys() -> Iterator[None]:
    """The owner's keys are not in the environment and the Keychain is not asked, until the block ends."""
    with pytest.MonkeyPatch.context() as patch:
        for name, value in _OWNERS.items():
            if os.environ.get(name) == value:
                patch.delenv(name)
        patch.setattr(secrets, "_from_keychain", lambda name: None)
        yield


@pytest.fixture(scope="session", autouse=True)
def _offline_run(request: pytest.FixtureRequest) -> Iterator[bool]:
    """Whether no test of the run needs the owner's keys: then they are out of reach for the whole run."""
    offline: bool = not any(_needs_keys(item) for item in request.session.items)
    if offline:
        with _without_the_owners_keys():
            yield True
    else:
        yield False


@pytest.fixture(autouse=True)
def _offline_test(request: pytest.FixtureRequest, _offline_run: bool) -> Iterator[None]:
    """In a run that holds tests which need the keys, every other test goes without them while it runs."""
    if _offline_run or _needs_keys(request.node):
        yield
        return
    with _without_the_owners_keys():
        yield
