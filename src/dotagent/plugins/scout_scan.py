"""The static scan of a plugin the scout found (item 710): what in the files Dot would keep can do harm, read before
any model reads them and whatever a model later makes of them.

It reads exactly the files an install keeps (the skills' folders, unpacked as :func:`dotagent.plugins.gallery.unpack`
unpacks them, whose hash the candidate carries), and finds:

- code: a script (by its extension or its mode), which a run may be told to run, and a binary, which no skill needs;
- what hides from a reader: invisible or direction-changing Unicode, and long base64, where an instruction can sit
  unseen by the owner and read by a model;
- what turns a model against its owner: text that tells it to set aside its instructions or to keep something from
  the owner, a shell fed from the network, an address that collects what is sent to it, the places credentials are
  kept, and switches of Dot's safety.

A finding is ``high`` when one alone should make the owner say no, ``medium`` when it makes the plugin riskier
(scripts are common in good skills: Anthropic's own PDF skill has them), ``note`` when it is worth knowing. The review
model reads the findings beside the text (:mod:`dotagent.plugins.scout_review`), and a plugin with a ``high`` one is
never shown as low risk.
"""

import codecs
import os
import re
import stat
from pathlib import Path

from dotagent.plugins.scout_types import MAX_FINDINGS, Finding, Severity

_SNIPPET_CHARS: int = 100
_SCRIPT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".rb",
        ".pl",
        ".php",
        ".ps1",
        ".bat",
        ".cmd",
        ".applescript",
        ".scpt",
        ".swift",
        ".go",
        ".lua",
    }
)
_BINARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".jar",
        ".class",
        ".wasm",
        ".node",
        ".o",
        ".a",
        ".pyc",
        ".app",
        ".pkg",
        ".dmg",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".7z",
    }
)
_MEDIA_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".pdf", ".ttf", ".otf", ".woff", ".woff2"}
)
# Characters a reader does not see and a model reads: zero-width ones, direction overrides and isolates, the tag block.
_HIDDEN: re.Pattern[str] = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\U000e0000-\U000e007f]")
_BASE64: re.Pattern[str] = re.compile(r"[A-Za-z0-9+/]{300,}={0,2}")
_RULES: tuple[tuple[str, Severity, re.Pattern[str]], ...] = (
    (
        # Makers install their own tools this way too (Hugging Face's CLI): the review weighs whose address it is.
        "pipe-to-shell",
        "medium",
        re.compile(r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", re.I),
    ),
    (
        "override",
        "high",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)*(?:previous|prior|earlier|above|"
            r"system|other)\s+(?:instructions|rules|prompts?|guidelines)\b",
            re.I,
        ),
    ),
    (
        "hide-from-owner",
        "high",
        re.compile(
            # What a run hides from its owner, not what it spares them ("do not tell the user they need to adopt...").
            r"\b(?:do\s+not|don't|never)\s+(?:tell|inform|show|mention\s+(?:this\s+)?to|let)\s+the\s+(?:user|owner|human)"
            r"\s*(?:[.,;:)]|$|about\b|that\s+you\b|what\s+you\b|you\b|know\b|see\b)",
            re.I,
        ),
    ),
    (
        # Acting "without asking the user": an order to keep the owner out, or, more often, what a skill warns against
        # ("never publish without asking the user first", which is left out by its negation). The review weighs it.
        "without-the-owner",
        "medium",
        re.compile(r"\bwithout\s+(?:telling|asking|informing|notifying)\s+the\s+(?:user|owner|human)\b", re.I),
    ),
    (
        "collector",
        "high",
        re.compile(
            # Services that collect what anyone sends them, which no skill's own work needs.
            r"(?:webhook\.site|requestbin|pipedream\.net|interactsh|burpcollaborator|pastebin\.com|transfer\.sh)",
            re.I,
        ),
    ),
    (
        # A tunnel to this Mac from the internet: migration and testing skills open one; it is a door all the same.
        "tunnel",
        "medium",
        re.compile(
            r"\b(?:ngrok(?:-free)?\.(?:io|app)|ngrok\s+http|cloudflared\s+tunnel|localtunnel|serveo\.net)", re.I
        ),
    ),
    (
        # Chat webhooks: an alerting skill names them for the owner's own channel; the review weighs it.
        "chat-webhook",
        "medium",
        re.compile(r"(?:discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services)", re.I),
    ),
    (
        # Where credentials are kept: a skill about the tool that keeps them names them too (AWS's own files).
        "credentials",
        "medium",
        re.compile(
            r"(?:~/\.ssh\b|\bid_(?:rsa|ed25519|ecdsa)\b|\.aws/credentials|\.netrc\b|\bsecurity\s+find-(?:generic|internet)-password"
            r"|Login\s+Data\b|cookies\.sqlite|Keychains?/|\.gnupg\b|\bwallet\.dat\b)",
            re.I,
        ),
    ),
    (
        "safety-switch",
        "medium",
        re.compile(
            r"(?:dangerously[-_]skip|--no-verify\b|\byolo\b|disable\s+(?:the\s+)?(?:sandbox|safety|approvals?|guard)"
            r"|bypass\s+(?:the\s+)?(?:sandbox|safety|approvals?|review))",
            re.I,
        ),
    ),
    (
        "secrets-in-env",
        "medium",
        re.compile(
            r"\b(?:printenv|env\s*\||os\.environ\b|process\.env\b)[^\n]{0,80}(?:curl|wget|requests|fetch)", re.I
        ),
    ),
)


def _snippet(text: str, start: int, end: int) -> str:
    """The words around a finding on one line, hidden characters shown by their code, at most :data:`_SNIPPET_CHARS`."""
    piece: str = " ".join(text[max(0, start - 30) : min(len(text), end + 30)].split())
    shown: str = _HIDDEN.sub(lambda found: f"<U+{ord(found.group()):04X}>", piece)
    return shown[:_SNIPPET_CHARS]


def _text_findings(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    hidden: re.Match[str] | None = _HIDDEN.search(text)
    if hidden is not None:
        count: int = len(_HIDDEN.findall(text))
        detail: str = (
            f"{count} invisible or direction-changing characters, first: {_snippet(text, hidden.start(), hidden.end())}"
        )
        findings.append(Finding(rule="hidden-unicode", severity="high", path=relative, detail=detail[:200]))
    blob: re.Match[str] | None = _BASE64.search(text)
    if blob is not None:
        findings.append(
            Finding(
                rule="base64",
                severity="medium",
                path=relative,
                detail=f"{len(blob.group())} characters of base64: {blob.group()[:40]}…",
            )
        )
    for rule, severity, pattern in _RULES:
        found: re.Match[str] | None = next(
            (match for match in pattern.finditer(text) if not (rule in _NEGATED and _negated(text, match.start()))),
            None,
        )
        if found is not None:
            findings.append(
                Finding(rule=rule, severity=severity, path=relative, detail=_snippet(text, found.start(), found.end()))
            )
    return findings


# The rules a negation before them turns into the opposite: "never ... without asking the user" protects the owner.
_NEGATED: frozenset[str] = frozenset({"without-the-owner"})
_NEGATION: re.Pattern[str] = re.compile(r"\b(?:never|not|don't|do\s+not|must\s+not|no)\b", re.I)


def _negated(text: str, start: int) -> bool:
    """Whether the sentence a finding at ``start`` is in says, before it, that it must not happen."""
    sentence_start: int = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), start - 80)
    return _NEGATION.search(text, sentence_start, start) is not None


# A stretch of printable ASCII (and line breaks and tabs) long enough to be words, in a file that holds a NUL.
_PLAIN_RUN: re.Pattern[bytes] = re.compile(rb"[\t\n\r\x20-\x7e]{24,}")


def runnable(path: Path) -> bool:
    """Whether a run may be told to run ``path``: a script by its suffix, or a file marked executable."""
    return path.suffix.lower() in _SCRIPT_SUFFIXES or bool(os.lstat(path).st_mode & stat.S_IXUSR)


def text_of(path: Path, limit: int | None = None) -> str | None:
    """The text of ``path`` (all of it: the scan reads every byte; or its first ``limit`` bytes), whatever its name
    says: None when it is not UTF-8 or holds a NUL, as an image, a font or a compiled file does (the review of the
    thirteenth push: a script named ``setup.png`` was taken for an image by its name, and neither scanned nor shown to
    the review)."""
    raw: bytes = path.read_bytes()[:limit]
    if b"\0" in raw:
        return None
    try:
        # Incremental, so that a character the limit cuts in two does not make the file not text.
        return codecs.getincrementaldecoder("utf-8")().decode(raw, final=False)
    except UnicodeDecodeError:
        return None


def _plain_runs(path: Path) -> str:
    """The runs of plain text (:data:`_PLAIN_RUN` or more printable ASCII characters) in the bytes of ``path``, one
    to a line: what a file that is not text as a whole still says in words."""
    raw: bytes = path.read_bytes()
    return "\n".join(run.decode("ascii") for run in _PLAIN_RUN.findall(raw))


def _file_findings(path: Path, relative: str) -> list[Finding]:
    suffix: str = path.suffix.lower()
    mode: int = os.lstat(path).st_mode
    findings: list[Finding] = []
    if suffix in _BINARY_SUFFIXES:
        return [Finding(rule="binary", severity="high", path=relative, detail="a compiled or packed file")]
    if suffix in _SCRIPT_SUFFIXES or mode & stat.S_IXUSR:
        findings.append(
            Finding(rule="script", severity="medium", path=relative, detail="code a run may be told to run")
        )
    text: str | None = text_of(path)
    media: bool = suffix in _MEDIA_SUFFIXES and suffix != ".svg"
    if text is None:
        if media:
            # Named as an image or a font and holding a NUL: usually what it says, but a NUL does not make the text
            # that follows it unread by a run that reads the file as text, so the runs of plain text in it are scanned.
            runs: str = _plain_runs(path)
            inside: list[Finding] = _text_findings(relative, runs) if runs else []
            if inside:
                # Disguised is as grave as the worst it hides: a long run of hex in a PDF stream or a path in a
                # photo's metadata is a medium finding, and does not make the plugin high by itself.
                worst: Severity = "high" if any(found.severity == "high" for found in inside) else "medium"
                inside.insert(
                    0,
                    Finding(
                        rule="disguised",
                        severity=worst,
                        path=relative,
                        detail=f"plain text in a file named as {suffix} that is not text (a NUL byte, or not UTF-8)",
                    ),
                )
            return [*findings, *inside]
        return [*findings, Finding(rule="binary", severity="high", path=relative, detail="not text, not a known kind")]
    if media:
        # Named as an image, a document or a font, and text: what a run reads or runs, under a name that hides it.
        findings.append(
            Finding(rule="disguised", severity="high", path=relative, detail=f"text in a file named as {suffix}")
        )
    return [*findings, *_text_findings(relative, text)]


def scan(skills: Path) -> tuple[Finding, ...]:
    """The findings in the files under ``skills`` (the plugin's kept skills), the most severe first, at most
    :data:`MAX_FINDINGS`. Raises OSError when a file cannot be read."""
    findings: list[Finding] = []
    for root, folders, names in os.walk(skills):
        folders.sort()
        for name in sorted(names):
            path: Path = Path(root) / name
            findings.extend(_file_findings(path, path.relative_to(skills).as_posix()))
    order: dict[Severity, int] = {"high": 0, "medium": 1, "note": 2}
    kept: list[Finding] = sorted(_scripts_by_skill(findings), key=lambda one: (order[one.severity], one.path, one.rule))
    return tuple(kept[:MAX_FINDINGS])


def _scripts_by_skill(findings: list[Finding]) -> list[Finding]:
    """``findings`` with the scripts of each skill told once (how many, and the first names): a skill with forty
    scripts would otherwise fill the list and push out what matters more."""
    scripts: dict[str, list[str]] = {}
    others: list[Finding] = []
    for finding in findings:
        if finding.rule == "script":
            scripts.setdefault(finding.path.split("/", 1)[0], []).append(finding.path)
        else:
            others.append(finding)
    for paths in scripts.values():
        names: str = ", ".join(path.split("/", 1)[-1] for path in paths[:4])
        more: str = f" and {len(paths) - 4} more" if len(paths) > 4 else ""
        detail: str = f"{len(paths)} script{'s' if len(paths) > 1 else ''} a run may be told to run: {names}{more}"
        others.append(Finding(rule="script", severity="medium", path=paths[0], detail=detail[:200]))
    return others


def worst(findings: tuple[Finding, ...]) -> Severity | None:
    """The most severe of ``findings``, None for none."""
    for severity in ("high", "medium", "note"):
        if any(finding.severity == severity for finding in findings):
            return severity
    return None
