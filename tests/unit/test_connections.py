"""Connect apps (item 278): the catalogue, what macOS's answers about Automation mean and the step each state offers, and
the sign-ins read from a Chrome profile's cookie file by host and name. macOS is asked for real only without a question
(about Finder, and about an application that does not exist)."""

import sqlite3
from pathlib import Path

import pytest

from dotagent.connections import mac
from dotagent.connections.catalogue import FOLDER_APPS, MAC_APPS, WEB_APPS, WebApp
from dotagent.connections.states import folder_state, folder_step, mac_step, web_state, web_step
from dotagent.connections.web import chrome_time, profile_cookies, signed_in
from dotagent.core.errors import DesktopError

_NOW: float = 1_800_000_000.0
_DAY_S: float = 86_400.0
_GITHUB: WebApp = next(app for app in WEB_APPS if app.id == "github")


def test_the_catalogue_names_each_application_once_and_signs_in_over_https() -> None:
    ids: list[str] = [app.id for app in MAC_APPS] + [app.id for app in FOLDER_APPS] + [app.id for app in WEB_APPS]
    assert len(ids) == len(set(ids))
    bundles: list[str] = [app.bundle_id for app in MAC_APPS] + [app.bundle_id for app in FOLDER_APPS]
    assert all(bundle.count(".") >= 2 for bundle in bundles)
    assert all(app.sign_in.startswith("https://") and app.domains and app.cookies for app in WEB_APPS)


def test_macos_answers_mean_a_state_and_each_state_offers_its_step() -> None:
    assert [mac.automation_answer(status, "com.example.app") for status in (0, -1744, -1743, -600)] == [
        "allowed",
        "not_asked",
        "refused",
        "not_open",
    ]
    with pytest.raises(DesktopError):
        mac.automation_answer(-50, "com.example.app")
    assert [mac_step(state) for state in ("allowed", "not_asked", "not_open", "refused", "not_installed")] == [
        None,
        "ask",
        "ask",
        "settings",
        None,
    ]
    assert [web_state("own", True), web_state("own", False), web_state("attach", False)] == [
        "signed_in",
        "signed_out",
        "lent",
    ]
    assert [web_step("signed_out", False), web_step("signed_out", True), web_step("signed_in", False)] == [
        "sign_in",
        None,
        None,
    ]
    assert [folder_state(True, True), folder_state(True, False), folder_state(False, False)] == [
        "synced",
        "not_synced",
        "not_installed",
    ]
    assert [folder_step("not_synced"), folder_step("synced")] == ["open_app", None]


def test_macos_is_asked_about_automation_without_a_question() -> None:
    assert mac.automation_state("com.apple.finder") in {"allowed", "not_asked", "refused", "not_open"}
    assert mac.automation_state("com.example.no-such-application") == "not_open"
    assert mac.installed_app("com.example.no-such-application") is None


def _cookie_file(profile: Path, cookies: list[tuple[str, str, float | None]]) -> None:
    """A cookie file as Chrome keeps it, with the columns read: host, name and expiry (``None`` for a session's)."""
    path: Path = profile / "Default" / "Network" / "Cookies"
    path.parent.mkdir(parents=True)
    connection: sqlite3.Connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE cookies (host_key TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,"
            " expires_utc INTEGER NOT NULL, has_expires INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?, ?, '', ?, ?)",
            [
                (host, name, 0 if expires is None else chrome_time(expires), 0 if expires is None else 1)
                for host, name, expires in cookies
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_a_sign_in_is_read_from_the_profile_by_host_and_name(tmp_path: Path) -> None:
    assert profile_cookies(tmp_path / "empty", _NOW) == frozenset()
    _cookie_file(
        tmp_path / "profile",
        [
            ("github.com", "user_session", _NOW + _DAY_S),
            (".slack.com", "d", _NOW - _DAY_S),
            (".notion.so", "token_v2", None),
            ("notgithub.com", "user_session", _NOW + _DAY_S),
        ],
    )
    held: frozenset[tuple[str, str]] = profile_cookies(tmp_path / "profile", _NOW)
    assert held == {("github.com", "user_session"), (".notion.so", "token_v2"), ("notgithub.com", "user_session")}
    signed: list[str] = [app.id for app in WEB_APPS if signed_in(app, held)]
    assert signed == ["github", "notion"]
    assert not signed_in(_GITHUB, frozenset({("notgithub.com", "user_session")}))
    assert signed_in(_GITHUB, frozenset({(".gist.github.com", "user_session")}))
