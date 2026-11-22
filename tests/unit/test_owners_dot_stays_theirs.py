"""A test never reaches the owner's own Dot (item 1315): the folders Dot keeps its files in are the run's own, and a
test that points them at the owner's fails."""

import os
import pwd
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from dotagent.core.paths import chrome_data_dir, dot_home, log_dir, plugins_dir

_CONFTEST: Path = Path(__file__).resolve().parent.parent / "conftest.py"


def _a_pytest_run(folder: Path, test: str) -> subprocess.CompletedProcess[str]:
    """A pytest run of one test file, beside a copy of the shared conftest, with the variables of Dot unset."""
    (folder / "conftest.py").write_text(_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "test_inner.py").write_text(textwrap.dedent(test), encoding="utf-8")
    environment: dict[str, str] = {
        name: value for name, value in os.environ.items() if not name.startswith(("DOT_", "PYTEST_"))
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--import-mode=importlib", str(folder)],
        capture_output=True,
        text=True,
        cwd=folder,
        env=environment,
        check=False,
        timeout=300,
    )


def test_dots_folders_of_this_run_are_not_the_owners(request: pytest.FixtureRequest) -> None:
    if any(
        item.get_closest_marker(name)
        for item in request.session.items
        for name in ("live", "hosted", "internet", "desktop")
    ):
        pytest.skip("a run that holds a test of the owner's own Mac keeps the owner's folders")
    home: Path = Path(pwd.getpwuid(os.getuid()).pw_dir)
    owners: tuple[Path, ...] = (
        home / "Library" / "Application Support" / "Dot",
        home / "Library" / "Logs" / "Dot",
        home / "Library" / "Application Support" / "Google" / "Chrome",
    )
    for where in (dot_home(), log_dir(), plugins_dir(), chrome_data_dir()):
        assert not any(Path(os.path.realpath(where)).is_relative_to(Path(os.path.realpath(own))) for own in owners), (
            where
        )


def test_a_test_with_no_variables_set_uses_folders_of_the_run(tmp_path: Path) -> None:
    done = _a_pytest_run(
        tmp_path,
        """
        import os
        from pathlib import Path

        from dotagent.core.paths import dot_home, log_dir, plugins_dir

        def test_isolated() -> None:
            for where in (dot_home(), log_dir(), plugins_dir()):
                parts = Path(os.path.realpath(where)).parts
                assert "Library" not in parts and any(part.startswith("pytest-of-") for part in parts), where
        """,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_test_that_points_dots_home_at_the_owners_fails(tmp_path: Path) -> None:
    done = _a_pytest_run(
        tmp_path,
        """
        import os
        import pwd
        from pathlib import Path

        from dotagent.core.paths import dot_home

        def test_reaches(monkeypatch) -> None:
            owners = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "Application Support" / "Dot"
            monkeypatch.setenv("DOT_HOME", str(owners))
            dot_home()
        """,
    )
    assert done.returncode != 0, done.stdout
    assert "on the owner's own" in done.stdout, done.stdout


def test_a_variable_the_run_was_given_stays_as_given(tmp_path: Path) -> None:
    given: Path = tmp_path / "given"
    given.mkdir()
    folder: Path = tmp_path / "inner"
    folder.mkdir()
    (folder / "conftest.py").write_text(_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "test_inner.py").write_text(
        textwrap.dedent(
            f"""
            from dotagent.core.paths import dot_home, log_dir

            def test_given() -> None:
                assert str(dot_home()) == {str(given)!r}
                assert str(log_dir()) != {str(given)!r}
            """
        ),
        encoding="utf-8",
    )
    environment: dict[str, str] = {
        name: value for name, value in os.environ.items() if not name.startswith(("DOT_", "PYTEST_"))
    }
    environment["DOT_HOME"] = str(given)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--import-mode=importlib", str(folder)],
        capture_output=True,
        text=True,
        cwd=folder,
        env=environment,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
