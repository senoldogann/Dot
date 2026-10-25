"""A line that names the Keychain service of Dot's own vault asks in every preset (item 621). Dot's tools keep the
vault's secrets from the model; the Keychain does not, and a program of the run's that asks it for them can read them
(``security`` is refused by its name, but ``python3 -c`` can run it). What the guard can read of such a program is the
name of the service, which the program has to say: a line, or a script it runs, that names ``dot-vault`` is a question,
however the program is started. What it does not read (a name put together from parts, a script in a file) is said on
the safety page. The lines here are data for the guard's parser: none is run."""

from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.policy.engine import decide
from dotagent.policy.paths import expand_protected
from dotagent.policy.shell import ShellAssessment, assess_applescript, assess_shell
from dotagent.policy.types import ActionInfo, Preset, Verdict

_PRESETS: tuple[Preset, ...] = ("aggressive", "balanced", "cautious")
_READ: str = "find-generic-password -s dot-vault -a vlt_0000_abcdef -w"
_NAMING_THE_SERVICE: list[str] = [
    "python3 -c \"import subprocess; print(subprocess.check_output(['/usr/bin/security', 'find-generic-password', "
    "'-s', 'dot-vault', '-w']))\"",
    "node -e \"require('child_process').execSync('/usr/bin/security find-generic-password -s dot-vault -w')\"",
    'perl -e \'system("/usr/bin/security", "find-generic-password", "-s", "dot-vault", "-w")\'',
    "ruby -e 'puts `/usr/bin/security find-generic-password -s DOT-VAULT -w`'",
    "osascript -e 'do shell script \"/usr/bin/security find-generic-password -s dot-vault -w\"'",
    f"bash <<'EOF'\n/usr/bin/security {_READ}\nEOF",
    # Quotes the shell takes out, and a word that goes through a variable it sets.
    'echo dot-v""ault',
    "echo 'dot-'\"vault\"",
    "sh -c 'service=dot-vault; echo $service'",
    # A developer's own line names it too, and asks.
    "grep -rn dot-vault src",
]


def _assessed(command: str, workspace: Path) -> tuple[ShellAssessment, list[Verdict]]:
    protected: tuple[Path, ...] = expand_protected(load_default_config().policy.refused_paths, ())
    assessment: ShellAssessment = assess_shell(command, workspace, workspace, protected, ())
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=command, target=command, flags=assessment.flags
    )
    return assessment, [decide(info, "task", False, preset, [], None).verdict for preset in _PRESETS]


@pytest.mark.parametrize("command", _NAMING_THE_SERVICE)
def test_a_line_that_names_the_vaults_keychain_service_is_asked_about_in_every_preset(
    command: str, tmp_path: Path
) -> None:
    assessment, verdicts = _assessed(command, tmp_path)
    assert "protected_read" in assessment.flags, (command, assessment)
    assert all(verdict in ("ask", "deny") for verdict in verdicts), (command, verdicts)


def test_a_line_that_only_names_the_service_asks_and_is_not_refused(tmp_path: Path) -> None:
    for command in ("python3 -c \"print('dot-vault')\"", "echo dot-vault", "grep -rn dot-vault src"):
        assessment, verdicts = _assessed(command, tmp_path)
        assert verdicts == ["ask", "ask", "ask"], (command, assessment)
        assert "secret_access" not in assessment.flags


def test_the_keychains_command_is_still_refused_by_its_name_in_every_preset(tmp_path: Path) -> None:
    assessment, verdicts = _assessed(f"security {_READ}", tmp_path)
    assert "secret_access" in assessment.flags
    assert verdicts == ["deny", "deny", "deny"]


@pytest.mark.parametrize(
    "command",
    [
        "ls ~/Documents",
        "echo vault",
        "git status",
        "python3 -c \"print('dot')\"",
        "security_tool --help",
        "echo dot-wallet",
    ],
)
def test_a_line_that_names_no_service_of_the_vault_is_asked_nothing_more_than_before(
    command: str, tmp_path: Path
) -> None:
    assessment, _ = _assessed(command, tmp_path)
    assert "protected_read" not in assessment.flags, (command, assessment)


@pytest.mark.parametrize(
    "script",
    [
        'do shell script "/usr/bin/security find-generic-password -s dot-vault -w"',
        'set s to "dot-vault"\nreturn s',
        "Application.currentApplication().doShellScript('/usr/bin/security find-generic-password -s dot-vault -w')",
    ],
)
def test_a_script_of_the_apple_script_tool_that_names_the_service_asks_in_every_preset(
    script: str, tmp_path: Path
) -> None:
    protected: tuple[Path, ...] = expand_protected(load_default_config().policy.refused_paths, ())
    assessment: ShellAssessment = assess_applescript(script, tmp_path, tmp_path, protected, ())
    assert "protected_read" in assessment.flags, (script, assessment)
