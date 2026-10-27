"""The redactor masks the secrets it knows by their forms and keeps the words around them, and leaves look-alikes alone
(items 214 and 241). Every sample is made up, and those in the form of a real service's key are put together at run
time, so no file holds one whole."""

import base64
import time

import pytest

from dotagent.core.text import redact_json, redact_known, redact_secrets

_MOMENT_S: float = 1.0
_LETTERS: str = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789aBcDeFgHiJ"
_PEM_BODY: str = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" + "x" * 64

_MASKED: list[tuple[str, str]] = [
    (f"cloned with {'gh' + 'p_' + _LETTERS[:36]} yesterday", "cloned with *** yesterday"),
    (f"token: {'github' + '_pat_' + _LETTERS[:30]}", "token: ***"),
    (f"key id {'AK' + 'IA' + 'QWERTYUIOPASDFGH'} in use", "key id *** in use"),
    (f"maps key {'AI' + 'za' + _LETTERS[:35]}", "maps key ***"),
    (f"bot {'xo' + 'xb-' + '1234567890-' + _LETTERS[:20]}", "bot ***"),
    (f"jwt {'ey' + 'JhbGciOiJIUzI1NiJ9'}.{'ey' + 'JzdWIiOiIxMjM0NSJ9'}.{_LETTERS[:22]}", "jwt ***"),
    (f"Authorization: Basic {base64.b64encode(b'alice:wonderland').decode()}", "Authorization: Basic ***"),
    (f'{{"Authorization": "Basic {base64.b64encode(b"bob:builder").decode()}"}}', '{"Authorization": "Basic ***"}'),
    (
        f"key:\n-----BEGIN {'PRIVATE KEY'}-----\n{_PEM_BODY}\n-----END {'PRIVATE KEY'}-----\nnext",
        "key:\n***\nnext",
    ),
    (f"-----BEGIN {'OPENSSH PRIVATE KEY'}-----\n{_PEM_BODY}", "***"),
    ("git clone https://alice:s3cr3t-p4ss@example.com/repo.git", "git clone https://alice:***@example.com/repo.git"),
    ("postgres://app:p@ss@db.example.com/main", "postgres://app:***@db.example.com/main"),
    ("password: hunter2", "password: ***"),
    ('{"db_password": "short"}', '{"db_password": "***"}'),
    (f"aws_secret_access_key = {_LETTERS[:40]}", "aws_secret_access_key = ***"),
    (f"X-Dot-Token: {_LETTERS[:24]}", "X-Dot-Token: ***"),
    (f"OPENAI {'sk-' + 'proj-' + _LETTERS[:24]}", "OPENAI ***"),
    (f"Authorization: Bearer {_LETTERS[:24]}", "Authorization: ***"),
    (f"api_key={_LETTERS[:12]}", "api_key=***"),
    (f"charge with {'sk' + '_live_' + _LETTERS[:24]}", "charge with ***"),
    (f"restricted {'rk' + '_test_' + _LETTERS[:24]}", "restricted ***"),
    (f"webhook {'wh' + 'sec_' + _LETTERS[:32]}", "webhook ***"),
    (f"push with {'gl' + 'pat-' + _LETTERS[:20]}", "push with ***"),
    (f"publish with {'np' + 'm_' + _LETTERS[:36]}", "publish with ***"),
    (f"hub {'h' + 'f_' + _LETTERS[:34]} read", "hub *** read"),
    (f"twilio {'S' + 'K' + '0123456789abcdef' * 2}", "twilio ***"),
    (f"mail {'S' + 'G.' + _LETTERS[:22] + '.' + _LETTERS[:43]}", "mail ***"),
    (
        f"AccountName=dot;{'Account' + 'Key'}={_LETTERS * 2}==;EndpointSuffix=core.windows.net",
        "AccountName=dot;AccountKey=***",
    ),
    ('"private_key_id": "' + "0123456789abcdef" * 2 + '"', '"private_key_id": "***"'),
    (f"Authorization: token {_LETTERS[:40]}", "Authorization: token ***"),
    (f'{{"authorization": "ApiKey {_LETTERS[:32]}"}}', '{"authorization": "ApiKey ***"}'),
    (f"Authorization: {_LETTERS[:40]}", "Authorization: ***"),
    (f"Cookie: sessionid={_LETTERS[:32]}; theme=dark\nnext", "Cookie: ***\nnext"),
    (f"Set-Cookie: sid={_LETTERS[:24]}; Path=/; HttpOnly", "Set-Cookie: ***"),
    # The code review of 2026-10-15 (Y3): a value in single quotes, a key with a word after its secret, a header of a
    # Python dict.
    (f"export API_KEY='{_LETTERS[:19]}'", "export API_KEY='***'"),
    ("DB_PASSWORD='hunter2hunter2' ./migrate.sh", "DB_PASSWORD='***' ./migrate.sh"),
    (f"SECRET_KEY={_LETTERS[:20]}", "SECRET_KEY=***"),
    (f"{{'Authorization': 'Token {_LETTERS[:19]}'}}", "{'Authorization': 'Token ***'}"),
    # A value given to a key that names a secret, in every form (Y3): an option and its value, a quoted value with
    # spaces, a short one in quotes, after a scheme, and the keys bearer, cookie and secret_key_base.
    ("mysql --password hunter2 --verbose", "mysql --password *** --verbose"),
    ("mysql --password=hunter2", "mysql --password=***"),
    (f"--api-key {_LETTERS[:12]}", "--api-key ***"),
    ("--token 'abc def ghi'", "--token '***'"),
    ('PASSWORD="correct horse battery staple"', 'PASSWORD="***"'),
    ('{"api_key": "abc123"}', '{"api_key": "***"}'),
    ("Authorization: Token short1", "Authorization: Token ***"),
    ("bearer=abcd1234", "bearer=***"),
    ("cookie=abcd1234", "cookie=***"),
    (f"secret_key_base: {_LETTERS[:24]}", "secret_key_base: ***"),
    # A value that only looks like a placeholder is one (item 690): a password that starts with a dollar sign, a
    # percent sign pair or an angle bracket, and a hint that has a digit in it.
    ("DB_PASSWORD=$Tr0ngPass1 ./run", "DB_PASSWORD=*** ./run"),
    ('{"password":"$Tr0ngPass1"}', '{"password":"***"}'),
    ("password=%secretkey%", "password=***"),
    ("api_key=<abc12345>", "api_key=***"),
    ("token=%tok3n%", "token=***"),
    ("token=${secrets.Tok3n}", "token=***}"),
]
_KEPT: list[str] = [
    '"max_tokens": 4096',
    "the tokenizer splits words, tokens: 12",
    "Basic usage of the tool",
    "http://localhost:8765/api/status",
    "ssh://git@github.com/owner/repo.git",
    "mail alice@example.com about https://example.com/a@b",
    "passwords: 3 were reused",
    "pwd=/Users/someone/project",
    "AKIA alone is no key",
    "the risk_live_dashboard01 panel",
    "hf_hub_download(repo_id, filename)",
    "npm_config_registry=https://registry.npmjs.org/",
    "Cookie: the browser keeps it until it expires",
    "Authorization: required for every request",
    # Words that end in "sk" before a dash are no key (Y4).
    "https://example.com/blog/task-management-best-practices",
    "feature/ask-for-confirmation-before-delete",
    "token_count=12345",
    "Plan the week on the task-management-2024 board.",
    "risk-free-returns-for-thirty-days",
    "mask-image-set-for-the-header-logo",
    # A key that names a secret given a placeholder, prose or nothing secret (Y3).
    "token: expires daily",
    '"token_type": "Bearer"',
    "password_policy=strict",
    "docker login --password-stdin < password.txt",
    "token=${GITHUB_TOKEN} and api_key=<your key>",
    "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    "token=none",
    "token=$GITHUB_TOKEN and password=${DB_PASSWORD} and secret=%SECRET_KEY%",
    "password=<your password> api_key=<your-api-key> token=<API_KEY>",
    "password=******** token=xxxxxxxx secret=.... key=…",
    "token={{ token }} secret=${{ secrets.DEPLOY }}",
]


@pytest.mark.parametrize(("text", "masked"), _MASKED)
def test_a_secret_of_a_known_form_is_masked_and_the_words_around_it_kept(text: str, masked: str) -> None:
    assert redact_secrets(text) == masked


@pytest.mark.parametrize("text", _KEPT)
def test_a_text_that_only_looks_like_a_secret_is_left_alone(text: str) -> None:
    assert redact_secrets(text) == text


def test_a_long_text_of_short_words_is_masked_in_a_moment() -> None:
    """Every event and error passes through the redactor, so no pattern may try each start of a long run of words
    joined by dots or dashes, or of the start of a web token, against the whole rest of it."""
    text: str = "a." * 20_000 + "b-" * 20_000 + "eyJ-" * 10_000 + " password: hunter2"
    started: float = time.monotonic()
    assert redact_secrets(text).endswith("password: ***")
    assert time.monotonic() - started < _MOMENT_S


def test_a_field_named_for_a_secret_is_masked_whatever_its_value_looks_like() -> None:
    assert redact_json(
        {
            "password": "hunter2hunter2",
            "db": {"api_key": "x"},
            "max_tokens": "4096",
            "note": "token",
            "token_type": "Bearer",
            "secret_key_base": "x",
        }
    ) == {
        "password": "***",
        "db": {"api_key": "***"},
        "max_tokens": "4096",
        "note": "token",
        "token_type": "Bearer",
        "secret_key_base": "***",
    }


def test_a_value_dot_holds_is_masked_wherever_it_stands_the_longest_first() -> None:
    """Review Y3: a secret Dot holds is masked word for word in any text, whatever stands around it; the longest of two
    that start alike is masked whole; one too short to tell from a word is left alone."""
    held: str = "MARKER-a1b2c3d4e5"
    longer: str = f"{held}f6g7"
    text: str = (
        f'use {held} in https://x.test/?k={held}&q=1, as \'{held}\' and {{"note": "{held}"}}, then {longer}; '
        "a MARKER alone; password: hunter2"
    )
    assert redact_known(text, (held, longer, "MARKER")) == (
        'use *** in https://x.test/?k=***&q=1, as \'***\' and {"note": "***"}, then ***; a MARKER alone; password: ***'
    )
    assert redact_known(text, ()) == redact_secrets(text)


def test_a_list_or_an_object_under_a_field_named_for_a_secret_is_masked_whole() -> None:
    """Item 690: the value of such a field is masked whatever shape it has; before, only a string was."""
    assert redact_json(
        {
            "token": ["abcdef123456", "second-value"],
            "auth": {"credentials": {"user": "alice", "keys": ["k1"]}},
            "passwords": {"main": "hunter2"},
            "max_tokens": [1, 2],
            "secret": "",
            "api_key": 12,
        }
    ) == {
        "token": ["***", "***"],
        "auth": {"credentials": {"user": "***", "keys": ["***"]}},
        "passwords": {"main": "hunter2"},
        "max_tokens": [1, 2],
        "secret": "",
        "api_key": 12,
    }


# The passwords command lines take as words of their own (item 726, the outside review of 2026-10-27), and the short
# names of a password; and the twins of each that hold none.
_COMMAND_LINES: list[tuple[str, str]] = [
    ("curl -u admin:hunter2xyz https://example.com", "curl -u admin:*** https://example.com"),
    ("curl -s --user 'admin:hunter2xyz' https://example.com", "curl -s --user 'admin:***' https://example.com"),
    ("curl --proxy-user proxy:b9xyzzz https://example.com", "curl --proxy-user proxy:*** https://example.com"),
    ("wget --user=admin:hunter2xyz https://example.com", "wget --user=admin:*** https://example.com"),
    ("docker login -u me -p S3cretPass9 registry.example", "docker login -u me -p *** registry.example"),
    ("podman login registry.example -p 'S3cret Pass9'", "podman login registry.example -p ***"),
    ("mysql -u root -pSuperSecret123 shop", "mysql -u root -p*** shop"),
    ("mysqldump -pSuperSecret123 shop > dump.sql", "mysqldump -p*** shop > dump.sql"),
    ("sshpass -p 'S3cret pass' ssh host", "sshpass -p *** ssh host"),
    ("gpg --batch --passphrase Hunter2xyz -c notes.txt", "gpg --batch --passphrase *** -c notes.txt"),
    ("htpasswd -b .htpasswd alice Hunter2xyz", "htpasswd -b .htpasswd alice ***"),
    ("htpasswd -c -b .htpasswd alice Hunter2xyz", "htpasswd -c -b .htpasswd alice ***"),
    ("aws configure set aws_secret_access_key Abc123def456", "aws configure set aws_secret_access_key ***"),
    ("DB_PASS=Hunter2xyz ./run", "DB_PASS=*** ./run"),
    ("PASS=Hunter2xyz", "PASS=***"),
    ("login pw=Hunter2xyz", "login pw=***"),
    ("pwd=Hunter2xyz", "pwd=***"),
    ("sessionid=abcdef123456", "sessionid=***"),
    ("session_id: abcDEF123456789", "session_id: ***"),
    # The spellings the independent review of the first push found: clusters of options, a value joined to its option,
    # an option before the password's, htpasswd with no file or with a cost, aws with its options before configure.
    ("curl -uadmin:Hunter2xyz https://example.com", "curl -uadmin:*** https://example.com"),
    ("curl -su admin:Hunter2xyz https://example.com", "curl -su admin:*** https://example.com"),
    ("curl -sSu admin:Hunter2xyz https://example.com", "curl -sSu admin:*** https://example.com"),
    ("docker login -u me -pS3cretPass9 registry.example", "docker login -u me -p*** registry.example"),
    ("sshpass -v -p S3cretPass9 ssh host", "sshpass -v -p *** ssh host"),
    ("htpasswd -nb alice Hunter2xyz", "htpasswd -nb alice ***"),
    ("htpasswd -nbB alice Hunter2xyz", "htpasswd -nbB alice ***"),
    ("htpasswd -b -C 12 .htpasswd alice Hunter2xyz", "htpasswd -b -C 12 .htpasswd alice ***"),
    (
        "aws --profile p configure set aws_secret_access_key Abc123def456",
        "aws --profile p configure set aws_secret_access_key ***",
    ),
    (
        "aws configure --profile p set aws_secret_access_key Abc123def456",
        "aws configure --profile p set aws_secret_access_key ***",
    ),
    ("MYSQL_PWD=/SuperSecret123 mysql", "MYSQL_PWD=*** mysql"),
    ("sshpass -pS3cretPass9 ssh host", "sshpass -p*** ssh host"),
    # A short name with a prefix names a password after ": " too, as YAML writes it (the review of the fifth push).
    ("DB_PASS: Tr0ub4dor&3", "DB_PASS: ***"),
    ("SMTP_PASS: hunter2Secret!", "SMTP_PASS: ***"),
    ("sshpass -f ~/.pass -p S3cretPass9 ssh host", "sshpass -f ~/.pass -p *** ssh host"),
]
_COMMAND_LINES_KEPT: list[str] = [
    "curl -u admin https://example.com",
    "git push -u origin main",
    "sort -u a:b",
    "mysql -u root -p shop",
    "docker login -u me --password-stdin registry.example",
    "sshpass -f ~/.pass ssh host",
    "PWD=/Users/someone/project",
    "cd $PWD && pwd",
    "bypass=on compass=north",
    "the pass rate: 98 percent",
    "passes: 3",
    "session_id: 42",
    "aws configure set region eu-west-1",
    "curl -X PUT -o out.txt https://example.com:8443/",
    "curl -u admin https://example.com:8443/",
    "htpasswd -v .htpasswd alice",
    "OLDPWD=/Users/someone",
    # The command sshpass runs has options of its own (the pre-push review of the second push).
    "sshpass -f ~/.pass ssh -p 2222 host",
    # A short name in a sentence (the pre-push review of the third push).
    "All tests pass: tests/unit/test_shell_senders.py",
    "--- PASS: TestParseConfigFile (0.00s)",
]


@pytest.mark.parametrize(("text", "masked"), _COMMAND_LINES)
def test_a_password_a_command_line_takes_as_a_word_is_masked(text: str, masked: str) -> None:
    assert redact_secrets(text) == masked


@pytest.mark.parametrize("text", _COMMAND_LINES_KEPT)
def test_a_command_line_that_gives_no_password_is_left_alone(text: str) -> None:
    assert redact_secrets(text) == text


def test_a_long_line_of_the_tools_whose_options_hold_a_password_is_masked_in_a_moment() -> None:
    """The pre-push review of the first push: a pattern that looked for a tool's name, then for ``login`` and the
    option anywhere after it, took time that grew with the cube of a long line of the names alone, which a page or a
    tool's answer can hold. Each command is read once now."""
    for text in (
        "docker login " * 4000,
        "mysql " * 8000,
        "curl " * 8000 + "-u" * 4000,
        "sshpass " * 8000,
        "aws configure set " * 4000,
        "htpasswd -b " * 4000,
        "curl -" + "a" * 50_000 + "u x",
    ):
        started: float = time.monotonic()
        redact_secrets(text)
        assert time.monotonic() - started < _MOMENT_S, text[:40]


def test_the_working_folder_of_an_object_is_kept_as_in_a_line() -> None:
    """The pre-push review of the second push: ``PWD=/Users/x`` stayed in a line, but ``{"PWD": "/Users/x"}`` was masked
    whole; a password given to it is masked in both."""
    assert redact_json({"PWD": "/Users/someone", "OLDPWD": "~/project"}) == {
        "PWD": "/Users/someone",
        "OLDPWD": "~/project",
    }
    assert redact_json({"pwd": "Hunter2xyz", "MYSQL_PWD": "/SuperSecret123"}) == {"pwd": "***", "MYSQL_PWD": "***"}
