"""Which variables of the login environment a command of a run is not given (items 555 and 1293): those whose name or
value says they hold a secret. The names are read without regard to case; the words are read as parts of the name
(``GH_PAT`` has the word ``PAT``, ``PATH`` has not) or, for the longer ones, anywhere in it."""

import re

# A name that holds one of these anywhere is a secret's, and nothing but the owner's ``[shell] pass_env`` keeps it.
_STRONG_PARTS: tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "COOKIE",
    "NETRC",
    "BEARER",
    "WEBHOOK",
)
# Words of a name (parts between underscores and other signs) that name a secret as well.
_STRONG_WORDS: frozenset[str] = frozenset({"DSN"})
# A name that holds one of these anywhere is a secret's unless it is one of :data:`_ALLOWED_NAMES` or starts with one of
# :data:`_ALLOWED_PREFIXES`: they are in the names of harmless variables too (``SSH_AUTH_SOCK``, ``XDG_SESSION_TYPE``).
_WEAK_PARTS: tuple[str, ...] = ("KEY", "AUTH", "SESSION")
# Words of a name that name a secret unless the name is allowed: ``PASS``, ``PAT`` (a personal access token; ``PATH`` is
# another word) and ``PWD`` (a password; the working folder's ``PWD`` and ``OLDPWD`` are in :data:`_ALLOWED_NAMES`).
_WEAK_WORDS: frozenset[str] = frozenset({"PASS", "PAT", "PWD"})
# What a name ends with when it is a secret key or a private key.
_KEY_ENDINGS: tuple[str, ...] = ("_SK", "_PK")
# The names of an address that carries the password of a service as a rule (item 1293).
_ADDRESS_NAMES: frozenset[str] = frozenset(
    {"DATABASE_URL", "REDIS_URL", "MONGODB_URI", "AMQP_URL", "CELERY_BROKER_URL"}
)
# The names of an address of which the value decides: one with a user and a password in it is a secret (a proxy's
# address, ``HTTPS_PROXY``, is where a password in an address shows up most).
_ADDRESS_ENDINGS: tuple[str, ...] = ("_URL", "_URI", "_PROXY")
_PASSWORD_IN_ADDRESS: re.Pattern[str] = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]*:[^/\s@]+@")
_WORD_SIGNS: re.Pattern[str] = re.compile(r"[^A-Z0-9]+")
# The names that hold a weak part and are the ordinary variables of a Mac and its tools: the agent of SSH keys, the
# sessions of the terminal and the desktop, the working folder. A strong part in the name still hides it
# (``KEYCHAIN_PASSWORD``).
_ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "SSH_AUTH_SOCK",
        "TERM_SESSION_ID",
        "ITERM_SESSION_ID",
        "SECURITYSESSIONID",
        "GPG_TTY",
        "PATH",
        "PWD",
        "OLDPWD",
    }
)
# A prefix excuses only the weak part it holds (``KEYCHAIN`` has ``KEY``, ``XDG_SESSION_`` has ``SESSION``,
# ``GIT_AUTHOR_`` has ``AUTH``): what follows it is read as any name is, so ``KEYCHAIN_PASS``, ``XDG_SESSION_KEY`` and
# ``GIT_AUTHOR_KEY`` go.
_ALLOWED_PREFIXES: tuple[str, ...] = ("XDG_SESSION_", "KEYCHAIN", "GIT_AUTHOR_", "HOMEBREW_NO_")


def _unexcused(name: str) -> str:
    """What of ``name`` is read for the weak parts, the weak words and the key endings: nothing of a name on the list
    of ordinary names, what follows the prefix of a name that starts with one on the list of prefixes, the whole of
    any other name."""
    if name in _ALLOWED_NAMES:
        return ""
    for prefix in _ALLOWED_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def hides_secret(name: str, value: str) -> bool:
    """Whether the variable ``name`` with ``value`` is held back from a command's environment: its name says it holds a
    secret (a token, a key, a password, a cookie, a session, an authorization, a webhook, a DSN, a secret or private
    key), or it is an address of a service that carries a password (a fixed few, and any ``_URL`` or ``_URI`` whose
    value has a user and a password in it). A name on the short list of ordinary variables (``SSH_AUTH_SOCK``,
    ``XDG_SESSION_*``, ``TERM_SESSION_ID``, ``KEYCHAIN*``, ``PATH``, ``PWD``, ``OLDPWD``, ``HOMEBREW_NO_*``...) is kept
    unless it also holds a strong part, or, under a prefix, a weak part or word in what follows the prefix
    (``KEYCHAIN_PASS``, ``XDG_SESSION_KEY``). The value is never kept or logged."""
    upper: str = name.upper()
    words: frozenset[str] = frozenset(part for part in _WORD_SIGNS.split(upper) if part)
    if any(part in upper for part in _STRONG_PARTS) or not _STRONG_WORDS.isdisjoint(words):
        return True
    rest: str = _unexcused(upper)
    if any(part in rest for part in _WEAK_PARTS) or rest.endswith(_KEY_ENDINGS):
        return True
    if not _WEAK_WORDS.isdisjoint(part for part in _WORD_SIGNS.split(rest) if part):
        return True
    if upper in _ADDRESS_NAMES:
        return True
    return upper.endswith(_ADDRESS_ENDINGS) and _PASSWORD_IN_ADDRESS.search(value) is not None
