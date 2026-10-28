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
its own.

The MCP server that comes with Dot is out of every test that did not ask for it (item 583), as
:func:`_no_builtin_servers` says. The folder Dot copies the pictures it sends into, ``~/Pictures/Dot``, is the owner's:
no test reaches it (item 617), as :func:`_owners_pictures_stay_theirs` says. Whether a model's server can be connected
to (item 769) is checked on this Mac alone: a server elsewhere counts as one that can be, as
:func:`_connection_checks_stay_on_this_mac` says."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from dotagent import secrets
from dotagent.channels.pictures import Pictures
from dotagent.channels.staging import STAGED_KEPT_S, Staging
from dotagent.config import load_default_config, providers_by_key
from dotagent.llm import reach
from dotagent.mcp import builtin

# The marks of the tests that send requests with the owner's keys or reach the world.
_NEEDS_KEYS: tuple[str, ...] = ("hosted", "internet", "live")
# The hosts on this Mac, whose connection a test may check (item 769).
_THIS_MAC: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
# The name the package of the web-research server is found under, read before any fixture changes it (item 583).
_REAL_WEB_RESEARCH_PACKAGE: str = builtin.WEB_RESEARCH_PACKAGE


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


@pytest.fixture(scope="session", autouse=True)
def _connection_checks_stay_on_this_mac() -> Iterator[None]:
    """A model's call that broke has its server's connection checked (item 769, :mod:`dotagent.llm.reach`): a test
    whose stand-in transport fails a call to ``api.example.com`` would look the name up and connect to it. A server on
    this Mac is checked as it is; any other is taken to be there, as before the check was made."""
    checking = reach.reaches

    async def here_only(url: str, timeout_s: float = reach.REACH_TIMEOUT_S) -> bool:
        return await checking(url, timeout_s) if urlsplit(url).hostname in _THIS_MAC else True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reach, "reaches", here_only)
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_builtin_servers() -> Iterator[None]:
    """The web-research server that comes with Dot (item 583) is not started by a test that did not ask for it: the
    package is installed where the tests run, so every runtime a test builds, and the suite builds hundreds, would
    start a Python process of its own, and its tools would be among those a test counts. Its package is looked for
    under a name nothing has, for the whole run; a test of the server asks for the real name back
    (:func:`web_research_comes`). Only this process is told: a ``dotx`` or a daemon a test starts finds the package, so
    its config says ``[builtin_mcp] web_research = false`` where it counts servers."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtin, "WEB_RESEARCH_PACKAGE", "dot_tests_have_no_web_research_package")
        yield


@pytest.fixture(scope="session", autouse=True)
def _owners_pictures_stay_theirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """The folder Dot copies the pictures it sends into (``~/Pictures/Dot``, item 617) is the owner's, not Dot's, and a
    channel that loads lists it and removes the old copies in it. No test reaches it, in two ways:

    - ``DOT_STAGING_DIR`` is a folder of the run's own for the whole run, so a ``Channels`` that was given none, and a
      daemon a test starts as a child process, use it (as ``DOT_CHROME_DIR`` keeps the owner's Chrome out of a test);
    - a ``Staging`` built in a test must stand in a pytest folder: one that does not (under the real home, or the
      default of a ``Channels`` given the owner's home) fails the test before the folder is touched, as
      :func:`channel_stand_ins.stop_file` refuses a home that is not a test's own."""
    inside: Path = Path(os.path.realpath(tmp_path_factory.getbasetemp()))
    built = Staging.__init__

    def refusing(self: Staging, root: Path, pictures: Pictures, kept_s: float = STAGED_KEPT_S) -> None:
        where: Path = Path(os.path.realpath(root))
        assert where.is_relative_to(inside), f"a test's staging folder is in a pytest folder, not {where} (the owner's)"
        built(self, root, pictures, kept_s)

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DOT_STAGING_DIR", str(tmp_path_factory.mktemp("staging") / "Dot"))
        patch.setattr(Staging, "__init__", refusing)
        yield


@pytest.fixture
def web_research_comes(monkeypatch: pytest.MonkeyPatch) -> None:
    """For a test of the server that comes with Dot: its package is found where it is installed, as it is for Dot."""
    monkeypatch.setattr(builtin, "WEB_RESEARCH_PACKAGE", _REAL_WEB_RESEARCH_PACKAGE)
