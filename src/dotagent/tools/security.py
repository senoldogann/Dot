"""Security scan (item 781, docs/security.md): the forms of a page the owner listed in ``[recon] owned_hosts``, read
and then tried with a small, benign set of payloads — SQL injection, reflected XSS, a login bypass, default
credentials.

It is a quick check of the owner's own site, not a scanner for someone else's: the host must be in ``owned_hosts``,
every form action must stay on that host, the payloads are read-only, the number of requests is capped, and the
report says what a form answered rather than dumping a page. It sends no exploit and writes nothing.
"""

import asyncio
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlencode, urljoin, urlsplit

from pydantic import Field

from dotagent.core.errors import ToolInputError, WebError
from dotagent.core.text import truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import (
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import ToolContext
from dotagent.tools.recon import Target, owned, parse_target
from dotagent.tools.web import checked_address
from dotagent.web.text import decode_body
from dotagent.web.types import FetchedDocument

SECURITY_TOOL: str = "security_scan"
_MAX_BODY_BYTES: int = 512_000
_SPACING_S: float = 0.15
_MAX_FORMS: int = 8
_MAX_FIELDS: int = 8
_EVIDENCE_CHARS: int = 180

# Payloads that ask a database a question without changing it: a quote to break the statement, a tautology to pass a
# check, and a false one to tell a real answer from a page that looks the same either way.
_SQLI: tuple[str, ...] = ("'", '"', "' OR '1'='1", "' OR '1'='1' -- ", "1' AND '1'='2", "admin'--")
# A marker a page that reflects its input unescaped cannot hide: the exact string comes back when it is not encoded.
_XSS: tuple[str, ...] = (
    "<script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "'><img src=x onerror=alert(1)>",
)
# A login that trusts the name or the password it is given; the password is a junk value.
_LOGIN: tuple[str, ...] = ("' OR '1'='1' -- ", "admin'--", "' OR 1=1#", '" OR ""="')
# The first credentials a fresh install leaves behind; the list is short on purpose (no credential stuffing).
_DEFAULT_CREDS: tuple[tuple[str, str], ...] = (
    ("admin", "admin"),
    ("admin", "password"),
    ("admin", "admin123"),
    ("root", "root"),
    ("test", "test"),
    ("administrator", "administrator"),
    ("admin", "123456"),
)
_SQL_ERRORS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"you have an error in your sql syntax",
        r"warning:\s+mysql",
        r"mysqli?_",
        r"pg_query\(\)",
        r"postgresql.*error",
        r"sqlite.*error",
        r"unrecognized token",
        r"unclosed quotation mark",
        r"quoted string not properly terminated",
        r"sqlstate\[",
        r"ora-\d{5}",
        r"microsoft ole db",
        r"odbc sql server driver",
        r"syntax error at or near",
    )
)
_LOGIN_FAIL: re.Pattern[str] = re.compile(
    r"invalid|incorrect|wrong|failed|denied|not found|try again|geçersiz|hatalı|yanlış", re.IGNORECASE
)
_LOGIN_OK: re.Pattern[str] = re.compile(
    r"log\s*out|sign\s*out|dashboard|welcome|my account|profile|çıkış|hoş geldin", re.IGNORECASE
)
# A redirect back to the login, or to an error, is a refusal even when its words do not say "invalid".
_LOGIN_WALL: re.Pattern[str] = re.compile(r"log\s*in|sign\s*in|error|denied", re.IGNORECASE)
_IGNORED_INPUTS: frozenset[str] = frozenset({"submit", "button", "reset", "image", "file"})
_FILLABLE: frozenset[str] = frozenset(
    {"text", "search", "email", "url", "tel", "number", "textarea", "select", "password"}
)


class SecurityArgs(ToolArgs):
    target: str = Field(
        min_length=3,
        max_length=300,
        description=(
            "The page with the forms, on a host in `[recon] owned_hosts`: a URL (`https://example.com/login`) or a "
            "bare domain."
        ),
    )
    depth: Literal["forms", "payloads"] = Field(
        default="payloads",
        description=(
            "`forms` lists the forms and their fields without sending a payload; `payloads` tries SQL injection, "
            "reflected XSS, a login bypass and default credentials on every field it can."
        ),
    )
    max_attempts: int = Field(
        default=40,
        ge=1,
        le=120,
        description="The most form submissions the test sends; each one waits for the answer before the next.",
    )


@dataclass(frozen=True)
class _Field:
    """One field of a form: its name, its kind and the value the page gives it."""

    name: str
    kind: str
    value: str


@dataclass(frozen=True)
class _Form:
    """A form's action, method and fields, with the action resolved against the page."""

    action: str
    method: str
    fields: tuple[_Field, ...]


@dataclass(frozen=True)
class _Attempt:
    """One payload in one place: the data to send, and what to say about it."""

    form: _Form
    kind: str
    where: str
    payload: str
    data: Mapping[str, str]


@dataclass(frozen=True)
class _Finding:
    """What a form's answer showed, in the words of the report."""

    severity: str
    kind: str
    where: str
    evidence: str


@dataclass
class _Building:
    """A form the parser is inside of."""

    action: str
    method: str
    fields: list[_Field]


class _Forms(HTMLParser):
    """The forms of a page: their action and method, and every field that carries a name."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[tuple[str, str, list[_Field]]] = []
        self._form: _Building | None = None
        self._select: _Field | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values: dict[str, str] = {name.lower(): (value or "") for name, value in attrs}
        if tag == "form":
            self._form = _Building(values.get("action", ""), values.get("method", "get").lower(), [])
            return
        if self._form is None:
            return
        fields: list[_Field] = self._form.fields
        if tag == "input":
            name: str = values.get("name", "").strip()
            kind: str = values.get("type", "text").strip().lower() or "text"
            if name and kind not in _IGNORED_INPUTS:
                fields.append(_Field(name, kind, values.get("value", "")))
        elif tag == "textarea":
            name = values.get("name", "").strip()
            if name:
                fields.append(_Field(name, "textarea", ""))
        elif tag == "select":
            name = values.get("name", "").strip()
            self._select = _Field(name, "select", "") if name else None
        elif tag == "option" and self._select is not None:
            if not self._select.value or "selected" in values:
                self._select = _Field(self._select.name, "select", values.get("value", ""))
        elif tag == "button":
            name = values.get("name", "").strip()
            kind = values.get("type", "submit").strip().lower() or "submit"
            if name and kind == "submit":
                fields.append(_Field(name, "submit", values.get("value", "")))

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select is not None and self._form is not None:
            self._form.fields.append(self._select)
            self._select = None
        elif tag == "form" and self._form is not None:
            self.forms.append((self._form.action, self._form.method, self._form.fields))
            self._form = None


def forms_on(page_url: str, body: str) -> tuple[_Form, ...]:
    """The forms a page carries, their actions made absolute and only http(s) kept."""
    parser = _Forms()
    try:
        parser.feed(body)
    except (AssertionError, ValueError):
        return ()
    forms: list[_Form] = []
    for action, method, fields in parser.forms[:_MAX_FORMS]:
        resolved: str = urljoin(page_url, action or page_url)
        if urlsplit(resolved).scheme not in {"http", "https"}:
            continue
        forms.append(_Form(resolved, "post" if method == "post" else "get", tuple(fields[:_MAX_FIELDS])))
    return tuple(forms)


def _same_host(url: str, host: str) -> bool:
    """Whether ``url`` stays on ``host``; a form that posts elsewhere is not tried."""
    try:
        return (urlsplit(url).hostname or "").lower() == host.lower()
    except ValueError:
        return False


def _data(form: _Form, field: str, payload: str) -> dict[str, str]:
    """The form's fields with ``payload`` in ``field`` and every other field at the value the page gave it."""
    data: dict[str, str] = {}
    for item in form.fields:
        if item.name == field:
            data[item.name] = payload
        elif item.kind in {"checkbox", "radio"}:
            data[item.name] = item.value or "on"
        elif item.kind == "submit":
            continue
        else:
            data[item.name] = item.value
    return data


def _attempts(forms: Sequence[_Form], limit: int, host: str) -> tuple[_Attempt, ...]:
    """The payloads to try, form by form, up to ``limit``; login forms get bypass and default credentials, the
    others get SQL injection and a reflected XSS marker on each field that takes text."""
    attempts: list[_Attempt] = []
    for form in forms:
        if not _same_host(form.action, host):
            continue
        passwords: list[_Field] = [field for field in form.fields if field.kind == "password"]
        texts: list[_Field] = [field for field in form.fields if field.kind in _FILLABLE and field.kind != "password"]
        if passwords:
            username: _Field | None = next(
                (field for field in texts if field.kind in {"text", "email"}), texts[0] if texts else None
            )
            if username is None:
                continue
            password: _Field = passwords[0]
            for payload in _LOGIN:
                by_bypass: dict[str, str] = _data(form, username.name, payload)
                by_bypass[password.name] = "x"
                attempts.append(
                    _Attempt(
                        form=form,
                        kind="login",
                        where=f"{_path(form.action)} ({username.name})",
                        payload=payload,
                        data=by_bypass,
                    )
                )
            for name, secret in _DEFAULT_CREDS:
                data: dict[str, str] = _data(form, username.name, name)
                data[password.name] = secret
                attempts.append(
                    _Attempt(
                        form=form,
                        kind="default",
                        where=f"{_path(form.action)} ({username.name}/{password.name})",
                        payload=f"{name}/{secret}",
                        data=data,
                    )
                )
            continue
        for field in texts:
            for payload in _SQLI:
                attempts.append(
                    _Attempt(
                        form=form,
                        kind="sqli",
                        where=f"{_path(form.action)} ({field.name})",
                        payload=payload,
                        data=_data(form, field.name, payload),
                    )
                )
            for payload in _XSS:
                attempts.append(
                    _Attempt(
                        form=form,
                        kind="xss",
                        where=f"{_path(form.action)} ({field.name})",
                        payload=payload,
                        data=_data(form, field.name, payload),
                    )
                )
    return tuple(attempts[:limit])


def _path(url: str) -> str:
    """The address a report shows: the path and query, or the whole URL when it has no path."""
    parts = urlsplit(url)
    return f"{parts.path or '/'}{'?' + parts.query if parts.query else ''}"


def _answer_text(document: FetchedDocument) -> str:
    """The page's words, or empty when its charset is one Python does not know."""
    try:
        return decode_body(document.body, document.charset)
    except WebError:
        return ""


def _header(document: FetchedDocument, name: str) -> str:
    for key, value in document.headers:
        if key.lower() == name:
            return value
    return ""


def _sql_error(body: str) -> str:
    for pattern in _SQL_ERRORS:
        match = pattern.search(body)
        if match is not None:
            return _one_line(body[max(0, match.start() - 40) : match.end() + 80])
    return ""


def _one_line(text: str) -> str:
    return " ".join(html.unescape(text).split())[:_EVIDENCE_CHARS]


def _logged_in(document: FetchedDocument, body: str) -> str:
    """Why an answer looks like a successful login: its redirect, its cookie, or the words only a signed-in page
    has; empty when it looks like a refusal."""
    location: str = _header(document, "location")
    if 300 <= document.status < 400:
        if location and not _LOGIN_FAIL.search(location) and not _LOGIN_WALL.search(location):
            return f"answered {document.status} to {_one_line(location)}"
        return ""
    if _header(document, "set-cookie") and not _LOGIN_FAIL.search(body):
        return "set a cookie and held no refusal"
    if _LOGIN_OK.search(body) and not _LOGIN_FAIL.search(body):
        return "showed a signed-in page"
    return ""


def _judge(attempt: _Attempt, document: FetchedDocument, body: str) -> _Finding | None:
    if attempt.kind == "sqli":
        error: str = _sql_error(body)
        if error:
            return _Finding(
                "high" if "syntax" in error.lower() else "medium",
                "SQL injection",
                attempt.where,
                f'`{attempt.payload}` brought a database error: "{error}"',
            )
        return None
    if attempt.kind == "xss":
        if attempt.payload in body:
            return _Finding(
                "medium",
                "Reflected XSS",
                attempt.where,
                f"`{attempt.payload}` came back unescaped",
            )
        return None
    if attempt.kind == "login":
        reason: str = _logged_in(document, body)
        if reason:
            return _Finding("high", "Login bypass", attempt.where, f"`{attempt.payload}` {reason}")
        return None
    if attempt.kind == "default":
        reason = _logged_in(document, body)
        if reason:
            return _Finding(
                "high",
                "Default credentials",
                attempt.where,
                f"`{attempt.payload}` {reason}",
            )
    return None


async def _submit(ctx: ToolContext, form: _Form, data: Mapping[str, str], max_bytes: int) -> FetchedDocument | str:
    """Send the form's fields the way its method says, and return the answer or why it did not come."""
    try:
        if form.method == "post":
            return await ctx.runtime.web.post_form(form.action, data, max_bytes)
        query: str = urlencode(list(data.items()))
        separator: str = "&" if urlsplit(form.action).query else "?"
        return await ctx.runtime.web.get(f"{form.action}{separator}{query}", max_bytes)
    except (WebError, ToolInputError) as exc:
        return exc.message


def _render_forms(target: Target, page: FetchedDocument, forms: Sequence[_Form]) -> str:
    lines: list[str] = [f"Security scan of {target.host}: {len(forms)} form(s) on {_path(page.url)}.", ""]
    for form in forms:
        names: str = ", ".join(field.name for field in form.fields) or "no named field"
        lines.append(f"  {_path(form.action)} ({form.method.upper()}): {names}")
    lines.append("")
    lines.append('No payload was sent: `depth = "forms"` lists what a page would take.')
    return "\n".join(lines)


def _render(
    target: Target,
    page: FetchedDocument,
    forms: Sequence[_Form],
    attempts: Sequence[_Attempt],
    findings: Sequence[_Finding],
    skipped: int,
) -> str:
    lines: list[str] = [
        f"Security scan of {target.host}: {len(forms)} form(s) on {_path(page.url)}, {len(attempts)} attempt(s).",
        "",
    ]
    for form in forms:
        names: str = ", ".join(field.name for field in form.fields) or "no named field"
        lines.append(f"  {_path(form.action)} ({form.method.upper()}): {names}")
    lines.append("")
    if findings:
        lines.append("Findings")
        order: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
        for finding in sorted(findings, key=lambda item: order.get(item.severity, 3)):
            lines.append(f"  [{finding.severity}] {finding.kind} — {finding.where}: {finding.evidence}")
    else:
        lines.append("No sign of SQL injection, reflected XSS, a login bypass or default credentials in these forms.")
    if skipped:
        lines.append("")
        lines.append(f"{skipped} form(s) posted to another host and were not tried.")
    lines.append("")
    lines.append(
        "Benign, read-only payloads; nothing was written, dropped or taken. A quick check of a host in "
        "`[recon] owned_hosts`, not a substitute for a full assessment."
    )
    return "\n".join(lines)


def _classify(args: SecurityArgs, ctx: ToolContext) -> ActionInfo:
    """The action of a call: a high-risk one when payloads go out, a read when the forms are only listed."""
    url, flags = checked_address(args.target, ctx.runtime.config)
    host: str = urlsplit(url).hostname or url
    active: bool = args.depth == "payloads"
    summary: str = (
        f"Security scan: try the forms of {host} with benign payloads"
        if active
        else f"Security scan: list the forms of {host}"
    )
    return ActionInfo(
        tool=SECURITY_TOOL,
        kind="network",
        risk="high" if active else "low",
        summary=summary[:200],
        target=host,
        flags=flags,
    )


async def _run(args: SecurityArgs, ctx: ToolContext) -> ToolResult:
    try:
        checked, _flags = checked_address(args.target, ctx.runtime.config)
    except ToolInputError as exc:
        return fail(exc.message)
    parsed: Target | str = parse_target(checked)
    if isinstance(parsed, str):
        return fail(parsed)
    target: Target = parsed
    hosts: Sequence[str] = ctx.runtime.config.recon.owned_hosts
    if not owned(target.host, hosts):
        return fail(
            f"{target.host} is not in [recon] owned_hosts, so its forms are not tested. Add the host you own there "
            "first (a name covers its subdomains)."
        )
    limit: int = min(ctx.runtime.config.web.fetch_max_bytes, _MAX_BODY_BYTES)
    try:
        page: FetchedDocument = await ctx.runtime.web.get(target.url, limit)
    except WebError as exc:
        return fail(exc.message)
    body: str = _answer_text(page)
    forms: tuple[_Form, ...] = forms_on(page.url, body)
    if not forms:
        return mark_untrusted(
            ok(
                truncate_tail(
                    f"Security scan of {target.host}: no form was found on {_path(page.url)}. Nothing was sent.",
                    ctx.output_chars,
                )
            )
        )
    if args.depth == "forms":
        return mark_untrusted(ok(_render_forms(target, page, forms)))
    attempts: tuple[_Attempt, ...] = _attempts(forms, args.max_attempts, target.host)
    findings: list[_Finding] = []
    seen: set[tuple[str, str]] = set()
    skipped: int = sum(1 for form in forms if not _same_host(form.action, target.host))
    for attempt in attempts:
        answer: FetchedDocument | str = await _submit(ctx, attempt.form, attempt.data, limit)
        if isinstance(answer, str):
            continue
        finding: _Finding | None = _judge(attempt, answer, _answer_text(answer))
        if finding is not None and (finding.kind, finding.where) not in seen:
            seen.add((finding.kind, finding.where))
            findings.append(finding)
        await asyncio.sleep(_SPACING_S)
    return mark_untrusted(
        ok(truncate_tail(_render(target, page, forms, attempts, findings, skipped), ctx.output_chars))
    )


def build_security_tool() -> RegisteredTool:
    """The security scan (item 781): the forms of a host of the owner's, tried with benign payloads."""
    return make_tool(
        name=SECURITY_TOOL,
        description=(
            "Test the forms of a page you own for SQL injection, reflected XSS, a login bypass and default "
            "credentials. Only a host in `[recon] owned_hosts` is touched, the payloads are read-only, and every "
            'form action must stay on that host. `depth = "forms"` lists the forms without sending anything.'
        ),
        args_model=SecurityArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=180,
        classify=_classify,
        run=_run,
    )
