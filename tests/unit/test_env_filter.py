"""The environment a command of a run is given holds none of the login environment's secrets and every ordinary
variable of a developer's Mac (items 555 and 1293): a table of names that stay and names that go, and of addresses
whose value decides."""

import pytest

from dotagent.tools.env_filter import hides_secret
from dotagent.tools.shell import child_env

# The ordinary variables of a login shell, of the tools a developer runs and of the desktop; all of them stay.
STAY: list[str] = [
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "EDITOR", "VISUAL",
    "PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "XPC_FLAGS", "XPC_SERVICE_NAME", "MallocSpaceEfficient",
    "OSLogRateLimit", "COMMAND_MODE", "SSH_AUTH_SOCK", "SSH_CONNECTION", "TERM_SESSION_ID", "ITERM_SESSION_ID",
    "SECURITYSESSIONID", "XDG_SESSION_TYPE", "XDG_SESSION_ID", "XDG_CONFIG_HOME", "GPG_TTY", "GNUPGHOME",
    "KEYCHAIN_PATH", "KEYCHAINS_DIR", "VOLTA_HOME", "CARGO_HOME", "RUSTUP_HOME", "NVM_DIR", "GOPATH", "GOROOT",
    "JAVA_HOME", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONUNBUFFERED", "MANPATH", "CLASSPATH", "NODE_PATH", "UV_PYTHON",
    "UV_CACHE_DIR", "UV_PROJECT_ENVIRONMENT", "npm_config_registry", "npm_config_cache", "npm_config_prefix",
    "npm_config_user_agent", "npm_config_local_prefix", "npm_lifecycle_event", "npm_package_name", "HOMEBREW_PREFIX",
    "HOMEBREW_CELLAR", "HOMEBREW_REPOSITORY", "HOMEBREW_NO_AUTO_UPDATE", "HOMEBREW_NO_ENV_HINTS", "GIT_PAGER",
    "GIT_TERMINAL_PROMPT", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "NO_COLOR", "PAGER", "DOT_RUN_SHELL", "DOT_HOME",
    "COMPASS_DIR", "PATTERN", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "http_proxy", "HTTPS_PROXY",
    "PATH_INFO", "PASSENGER_ROOT", "GIT_AUTHOR_DATE", "XDG_SESSION_CLASS", "XDG_SESSION_DESKTOP",
]  # fmt: skip
# Names that say they hold a secret; all of them go, in any case.
GO: list[str] = [
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "NPM_TOKEN", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "AWS_SESSION", "DB_PASSWORD", "PGPASSWORD", "MYSQL_PWD", "DB_PWD", "DB_PASS", "FTP_PASSWD",
    "GH_PAT", "PAT", "GITHUB_PAT", "AZURE_DEVOPS_PAT", "SOME_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS",
    "COOKIE", "SESSION_COOKIE", "NETRC", "SENTRY_DSN", "DSN", "RAILS_SESSION_SECRET", "SESSION", "SESSION_ID",
    "OAUTH_CLIENT", "AUTH", "AUTHORIZATION", "BEARER", "API_BEARER", "SLACK_WEBHOOK", "SLACK_WEBHOOK_URL",
    "DISCORD_WEBHOOK", "STRIPE_SK", "SOMETHING_PK", "SSH_PRIVATE_KEY", "KEYCHAIN_PASSWORD", "HOMEBREW_GITHUB_API_TOKEN",
    "XDG_SESSION_TOKEN", "DATABASE_URL", "REDIS_URL", "MONGODB_URI", "AMQP_URL", "CELERY_BROKER_URL",
    "npm_config__authtoken", "openai_api_key", "Github_Token", "GIT_AUTHOR_TOKEN", "PASSWORD_STORE_DIR",
    # A prefix excuses only the weak part it holds: what follows it is read as any name is (item 1293).
    "KEYCHAIN_PASS", "KEYCHAIN_PWD", "KEYCHAIN_PAT", "KEYCHAIN_KEY", "KEYCHAIN_AUTH", "KEYCHAIN_SK", "keychain_pass",
    "XDG_SESSION_KEY", "XDG_SESSION_AUTH", "XDG_SESSION_PASS", "GIT_AUTHOR_KEY", "GIT_AUTHOR_PASS", "GIT_AUTHOR_PAT",
]  # fmt: skip


@pytest.mark.parametrize("name", STAY)
def test_an_ordinary_variable_is_given_to_a_command(name: str) -> None:
    assert not hides_secret(name, "/some/value"), name
    # A few names are set by Dot itself (the terminal, the pager): they are there whatever the login said.
    assert name in child_env({name: "/some/value"}, [])


@pytest.mark.parametrize("name", GO)
def test_a_variable_whose_name_says_it_holds_a_secret_is_held_back(name: str) -> None:
    assert hides_secret(name, "/some/value"), name
    assert name not in child_env({name: "/some/value"}, [])


@pytest.mark.parametrize("name", GO)
def test_the_owner_can_still_pass_a_held_back_name_through_pass_env(name: str) -> None:
    assert child_env({name: "v"}, [name])[name] == "v"


# An address of a service: the value decides, but for the few names that carry a password as a rule.
ADDRESSES: list[tuple[str, str, bool]] = [
    ("API_URL", "https://api.example.com/v1", False),
    ("API_URL", "https://user:s3cret@api.example.com/v1", True),
    ("API_URL", "https://user@api.example.com/v1", False),
    ("API_URL", "https://user:@api.example.com/v1", False),
    ("PROFILE_URI", "postgres://app:pw@db.example.com/app", True),
    ("UV_INDEX_URL", "https://pypi.org/simple", False),
    ("UV_INDEX_URL", "https://me:pw@pypi.example.com/simple", True),
    ("REPO_URL", "git@github.com:me/repo.git", False),
    ("NOTE", "https://user:s3cret@api.example.com/v1", False),
    ("HTTP_PROXY", "http://user:pw@proxy.example.com:8080", True),
    ("https_proxy", "http://user:pw@proxy.example.com:8080", True),
    ("ALL_PROXY", "socks5://user:pw@proxy.example.com:1080", True),
    ("HTTPS_PROXY", "http://proxy.example.com:8080", False),
    ("NO_PROXY", "localhost,127.0.0.1,.example.com", False),
    ("MIRRORS_URL", "https://a.example/x,https://u:p@b.example/y", True),
    ("DATABASE_URL", "sqlite:///local.db", True),
    ("REDIS_URL", "", True),
]


@pytest.mark.parametrize(("name", "value", "goes"), ADDRESSES)
def test_an_address_goes_when_its_value_carries_a_password_or_its_name_says_it_does(
    name: str, value: str, goes: bool
) -> None:
    assert hides_secret(name, value) is goes
    assert (name in child_env({name: value}, [])) is not goes


def test_the_working_folders_variables_stay_and_a_password_ending_in_pwd_goes() -> None:
    assert not hides_secret("PWD", "/Users/me/project") and not hides_secret("OLDPWD", "/Users/me")
    assert hides_secret("MYSQL_PWD", "x") and hides_secret("pwd_file_password", "x")


def test_a_command_still_has_its_fixed_variables_and_the_ordinary_ones_of_the_login_environment() -> None:
    login: dict[str, str] = {
        "PATH": "/opt/homebrew/bin:/usr/bin",
        "HOME": "/Users/me",
        "SSH_AUTH_SOCK": "/private/tmp/agent.sock",
        "VOLTA_HOME": "/Users/me/.volta",
        "OPENAI_API_KEY": "sk-not-real",
        "DB_PWD": "x",
    }
    given: dict[str, str] = child_env(login, [])
    assert given["PATH"] == login["PATH"] and given["SSH_AUTH_SOCK"] == login["SSH_AUTH_SOCK"]
    assert given["VOLTA_HOME"] == login["VOLTA_HOME"] and given["TERM"] == "dumb" and given["NO_COLOR"] == "1"
    assert "OPENAI_API_KEY" not in given and "DB_PWD" not in given
