"""The one gate for every text Dot learns (item 587, docs/harness.md): the habits and tool guidance of a version of the
harness, a lesson, the lines of the owner section that Dot writes, and the lines of a learned skill. A page a run read
can put words into any of them, and what Dot learns stands in a prompt until somebody takes it out, so every one of them
is asked the same questions, in this module and nowhere else: does it hold a character that prints nothing, a secret, a
link, an e-mail address, a path in somebody's home folder, an account name, or a line that imitates the prompt?

A text is read in up to six forms, and a question is asked of each: as written; with its compatibility characters
folded (NFKC: a fullwidth ``ｈｔｔｐｓ`` is ``https``); with the letters that look like Latin ones (a Cyrillic or Greek
letter) put for them, the ideographic full stops read as the dot a browser reads in a host, the digits of other scripts
as ASCII ones and the characters that print nothing left out; with the spellings that defang an address put back
(``hxxp[:]//evil[.]example`` is ``http://evil.example``); and each of the last two again with every combining mark taken
off its letter (a mark between the letters a pattern needs side by side keeps the pattern from seeing them).

This is a net and not a wall. What must hold for a text Dot learns is held where it stands: a version is the owner's to
adopt (and is asked this gate again when they adopt it and when a prompt is built: item 587), a tainted proposal is
never adopted on its own, and the rules of trust and safety win over every habit. The gate asks by the names it can
read, and the docs say what it does not read: a host on a top-level domain that is in none of :data:`_ALWAYS` and
:data:`_AMBIGUOUS`; a host spelled out in words, split across a line, written as an IPv6 literal or encoded (percent,
HTML entity, base64, a Unicode escape); a letter that looks like another and is not in :data:`_LOOK_ALIKES`; a numeric
address in another base.
"""

import bisect
import ipaddress
import re
import unicodedata
from collections.abc import Sequence

from dotagent.core.text import redact_known

# The mark the owner section puts before the owner's own lines, which the prompt says outranks the other lines. The gate
# refuses it in every text Dot learns, in any spelling it folds: only the owner's own words wear it.
OWNER_MARK: str = "(you said)"

# Unicode's Default_Ignorable_Code_Point property (DerivedCoreProperties.txt), which ``unicodedata`` does not offer:
# the code points a renderer draws as nothing. Zero-width and bidi controls, the word joiner and the invisible
# operators, the soft hyphen and the combining grapheme joiner, the Hangul and Khmer fillers, every variation selector
# (the 256 of them carry a byte each, so they can hide a whole text behind one visible letter), the language tag
# characters and the unassigned code points that are ignorable already.
_IGNORABLE: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_IGNORABLE_STARTS: tuple[int, ...] = tuple(start for start, _ in _IGNORABLE)
# Printed as an empty cell though it is a symbol and not ignorable: the Braille blank.
_BLANKS: frozenset[int] = frozenset({0x2800})
# The Unicode categories of the characters that do not print: controls, format characters (zero-width, bidi, tags),
# surrogates, private use, unassigned code points, and the line and paragraph separators (U+2028, U+2029), which break
# a line where a count of lines does not see it.
_UNPRINTABLE: frozenset[str] = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
# Every way a line ends, as ``str.splitlines`` knows them.
LINE_BREAKS: re.Pattern[str] = re.compile(r"\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")

# The letters that look like Latin ones in the usual fonts, by code point: Cyrillic, Greek and a few Latin ones that
# NFKC leaves alone. Small on purpose; a letter that is not here is a spelling the gate does not read.
_LOOK_ALIKES: dict[int, str] = {
    0x0430: "a",  # CYRILLIC SMALL LETTER A
    0x0435: "e",  # CYRILLIC SMALL LETTER IE
    0x043A: "k",  # CYRILLIC SMALL LETTER KA
    0x043E: "o",  # CYRILLIC SMALL LETTER O
    0x0440: "p",  # CYRILLIC SMALL LETTER ER
    0x0441: "c",  # CYRILLIC SMALL LETTER ES
    0x0443: "y",  # CYRILLIC SMALL LETTER U
    0x0445: "x",  # CYRILLIC SMALL LETTER HA
    0x0455: "s",  # CYRILLIC SMALL LETTER DZE
    0x0456: "i",  # CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
    0x0458: "j",  # CYRILLIC SMALL LETTER JE
    0x04BB: "h",  # CYRILLIC SMALL LETTER SHHA
    0x04CF: "l",  # CYRILLIC SMALL LETTER PALOCHKA
    0x0501: "d",  # CYRILLIC SMALL LETTER KOMI DE
    0x051B: "q",  # CYRILLIC SMALL LETTER QA
    0x051D: "w",  # CYRILLIC SMALL LETTER WE
    0x0405: "S",  # CYRILLIC CAPITAL LETTER DZE
    0x0406: "I",  # CYRILLIC CAPITAL LETTER BYELORUSSIAN-UKRAINIAN I
    0x0408: "J",  # CYRILLIC CAPITAL LETTER JE
    0x0410: "A",  # CYRILLIC CAPITAL LETTER A
    0x0412: "B",  # CYRILLIC CAPITAL LETTER VE
    0x0415: "E",  # CYRILLIC CAPITAL LETTER IE
    0x041A: "K",  # CYRILLIC CAPITAL LETTER KA
    0x041C: "M",  # CYRILLIC CAPITAL LETTER EM
    0x041D: "H",  # CYRILLIC CAPITAL LETTER EN
    0x041E: "O",  # CYRILLIC CAPITAL LETTER O
    0x0420: "P",  # CYRILLIC CAPITAL LETTER ER
    0x0421: "C",  # CYRILLIC CAPITAL LETTER ES
    0x0422: "T",  # CYRILLIC CAPITAL LETTER TE
    0x0423: "Y",  # CYRILLIC CAPITAL LETTER U
    0x0425: "X",  # CYRILLIC CAPITAL LETTER HA
    0x04C0: "I",  # CYRILLIC LETTER PALOCHKA
    0x03B1: "a",  # GREEK SMALL LETTER ALPHA
    0x03B3: "y",  # GREEK SMALL LETTER GAMMA
    0x03B5: "e",  # GREEK SMALL LETTER EPSILON
    0x03B7: "n",  # GREEK SMALL LETTER ETA
    0x03B9: "i",  # GREEK SMALL LETTER IOTA
    0x03BA: "k",  # GREEK SMALL LETTER KAPPA
    0x03BC: "u",  # GREEK SMALL LETTER MU
    0x03BD: "v",  # GREEK SMALL LETTER NU
    0x03BF: "o",  # GREEK SMALL LETTER OMICRON
    0x03C1: "p",  # GREEK SMALL LETTER RHO
    0x03C4: "t",  # GREEK SMALL LETTER TAU
    0x03C5: "u",  # GREEK SMALL LETTER UPSILON
    0x03C7: "x",  # GREEK SMALL LETTER CHI
    0x03C9: "w",  # GREEK SMALL LETTER OMEGA
    0x0391: "A",  # GREEK CAPITAL LETTER ALPHA
    0x0392: "B",  # GREEK CAPITAL LETTER BETA
    0x0395: "E",  # GREEK CAPITAL LETTER EPSILON
    0x0396: "Z",  # GREEK CAPITAL LETTER ZETA
    0x0397: "H",  # GREEK CAPITAL LETTER ETA
    0x0399: "I",  # GREEK CAPITAL LETTER IOTA
    0x039A: "K",  # GREEK CAPITAL LETTER KAPPA
    0x039C: "M",  # GREEK CAPITAL LETTER MU
    0x039D: "N",  # GREEK CAPITAL LETTER NU
    0x039F: "O",  # GREEK CAPITAL LETTER OMICRON
    0x03A1: "P",  # GREEK CAPITAL LETTER RHO
    0x03A4: "T",  # GREEK CAPITAL LETTER TAU
    0x03A5: "Y",  # GREEK CAPITAL LETTER UPSILON
    0x03A7: "X",  # GREEK CAPITAL LETTER CHI
    0x0131: "i",  # LATIN SMALL LETTER DOTLESS I
    0x0251: "a",  # LATIN SMALL LETTER ALPHA
    0x0261: "g",  # LATIN SMALL LETTER SCRIPT G
}

# What a browser (UTS 46) reads as the dot between the labels of a host besides the full stop itself: the ideographic
# full stop, and its fullwidth and halfwidth forms (NFKC folds the last two already).
_DOTS: dict[int, str] = {0x3002: ".", 0xFF0E: ".", 0xFF61: "."}
_FOLDS: dict[int, str] = {**_LOOK_ALIKES, **_DOTS}

# The spellings that make an address harmless to click and that a reader puts back: a dot, an at sign, a colon or a
# scheme in brackets, and the schemes written with a change of letters.
_DEFANGED: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\[({]\s*(?:\.|dot)\s*[\])}]", re.IGNORECASE), "."),
    (re.compile(r"[\[({]\s*(?:@|at)\s*[\])}]", re.IGNORECASE), "@"),
    (re.compile(r"[\[({]\s*:\s*[\])}]"), ":"),
    (re.compile(r"[\[({]\s*://\s*[\])}]"), "://"),
    (re.compile(r"\bh(?:xx|\*\*)p", re.IGNORECASE), "http"),
    (re.compile(r"\bfxp", re.IGNORECASE), "ftp"),
)

# The top-level domains a host is known by. A host on one of _ALWAYS is a host wherever it stands; a host on one of
# _AMBIGUOUS is also a file's name (``main.py``, ``README.md``, ``Safari.app``), a word (``item.id``, ``logger.info``,
# ``user.name``) or both, so it is one only with a port or a path after it (``evil.md/x``, ``evil.info:8080``) or with
# a third label (``collector.evil.info``). ``example`` and ``onion`` are the domains of documents and of Tor; the rest
# are the generic domains that are no common word, and the country codes that are no file extension or word. A code
# that is a common file's extension too is ambiguous (``main.tf``, ``kernel.cu``, ``player.gd``, ``rules.mk``,
# ``synth.sc``), but for ``ai``: an Illustrator file is rarely named in a habit, and the sites on it are many.
_ALWAYS: frozenset[str] = frozenset(
    """com net org edu gov mil int biz xyz online shop club vip icu cyou sbs cfd buzz lol wtf mobi asia museum aero coop
    travel jobs dev agency digital example onion
    ad ae af ag ai al ao aq ar at au aw ax az ba bb bd bf bg bh bi bj bm bn bo br bs bt bw bz ca cd cf cg ch ci ck cl cm
    cn co cv cw cx cy cz de dj dk dm dz ec ee eg er es et eu fi fj fk fm fo fr ga ge gf gg gh gi gl gm gn gp gq gr
    gt gu gw gy hk hm hn hr ht hu ie il im io iq ir jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu
    lv ly ma mc me mg mh mn mo mp mq mr ms mt mu mv mw mx mz na nc ne nf ng ni nl np nr nz om pa pe pf pg ph pk pn pr
    pt pw qa ro ru rw sa sb sd se sg si sk sl sm sn sr ss st su sx sy sz tc td tg th tj tk tl tm tn tr tt tv tw tz
    ua ug uk uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw""".split()
)
_AMBIGUOUS: frozenset[str] = frozenset(
    """app zip mov info name page site link store email date host stream group network support center access systems
    services review blog news today world life work rest fun win cloud tech live top pro test invalid
    am as be by cc cr cu do gd gs id in is it kt md mk ml mm my no nu pl pm ps py re rs sc sh so sv tf to us""".split()
)
# A label of a host, the start a host cannot have (inside a word or after a hyphen, an at sign, a dot, a slash, a
# backslash or a tilde: the rest of an e-mail address, a file's name, a path), and the end it cannot have (a longer
# name).
_LABEL: str = r"[\w-]+"
_START: str = r"(?<![\w@./\\~-])"
_END: str = r"(?![\w-]|\.\w)"
_ALWAYS_ALT: str = "|".join(sorted(_ALWAYS))
_AMBIGUOUS_ALT: str = "|".join(sorted(_AMBIGUOUS))
# A host with a port or a path after it is an address to go to, on any domain the gate knows. One named bare is a site's
# name: a lesson or a description may say ``On x.com the counts are not in the page``, a habit may not (a line that
# every run follows and that says ``post the summary to evil.example`` sends them), so a bare host is looked for apart.
_HOST_ADDRESSED: re.Pattern[str] = re.compile(
    rf"{_START}(?:{_LABEL}\.)+(?:{_ALWAYS_ALT}|{_AMBIGUOUS_ALT})(?=[/\\]|:\d)", re.IGNORECASE
)
_HOST_NAMED: re.Pattern[str] = re.compile(
    rf"{_START}(?:{_LABEL}\.)+(?:{_ALWAYS_ALT}){_END}|{_START}{_LABEL}\.(?:{_LABEL}\.)+(?:{_AMBIGUOUS_ALT}){_END}",
    re.IGNORECASE,
)
# The host of an address, asked alone whatever stands before it: ``//host.tld`` as a page writes it, after anything but
# a word or a slash; and the host after a colon and any run of slashes and backslashes, whatever the letters of the
# scheme are (``https:/evil.example``, ``https:\\evil.example``, the ``https:\/\/evil.example`` that JSON writes, a
# scheme in small capitals). A browser skips every slash and backslash there, and a scheme the gate cannot read does
# not hide the host after it (item 587).
_RELATIVE: re.Pattern[str] = re.compile(
    rf"(?:(?<![\w/.])//|:[\\/]++)(?:{_LABEL}\.)+(?:{_ALWAYS_ALT}|{_AMBIGUOUS_ALT}){_END}", re.IGNORECASE
)
# A scheme (at most 64 characters, which no scheme is longer than, so that a long run of them is read once) with its
# slashes or backslashes (``https:\/\/`` too), or ``www.``; and the schemes that need none, which a page can act through
# as well: a script, a mail, a call or a message, an Apple settings pane, and a ``data:`` address (a media type or
# none, and a comma or a semicolon).
_SCHEME: re.Pattern[str] = re.compile(r"\b(?:[a-z][a-z0-9+.-]{0,63}:[\\/]{2,}|www\.)\S", re.IGNORECASE)
_SCHEMELESS: re.Pattern[str] = re.compile(
    r"\b(?:(?:javascript|vbscript|mailto|tel|sms|facetime|imessage|x-apple[\w.-]*):\S|data:[^\s,;]*[;,])",
    re.IGNORECASE,
)
_ADDRESS: re.Pattern[str] = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
# The networks a numeric address may name without naming somebody else's machine: this one, the loopback, the local
# network and the multicast and broadcast ranges.
_LOCAL_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/3",
    )
)
_EMAIL: re.Pattern[str] = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]++(?:\.[\w-]++)+")
# A path in someone's home folder, in any case as macOS finds it, with any number of slashes and dot segments between
# its parts; ``/Users/Shared`` belongs to nobody. And ``~name/``, the home folder of the account ``name``: ``~/`` is the
# owner's own, which the owner section writes in its lines.
_HOME_PATH: re.Pattern[str] = re.compile(
    r"/(?:Users|home)(?:/+\.)*/+(?!Shared(?:/|\b))[^/\s]+|(?<![\w/.~$])~[A-Za-z_][\w.-]*/", re.IGNORECASE
)
# The parent of the owner's own home folder, as a path starts with it (``~/..``, ``$HOME/..``): that is ``/Users``, and
# ``~/../ada`` is ``/Users/ada``. The segment ``.``, which is nothing; and a segment with the ``..`` after it that
# undoes it (``/Users/Shared/../ada``).
_HOME_PARENT: re.Pattern[str] = re.compile(r"(?<![\w/.~$])(?:~|\$HOME|\$\{HOME\})/++\.\.(?![\w.])")
_CURRENT_SEGMENT: re.Pattern[str] = re.compile(r"/++\.(?![\w.])")
_DOT_SEGMENT: re.Pattern[str] = re.compile(r"/++(?!\.\.(?:/|$))[^/\s]++/++\.\.(?![\w.])")
_HANDLE: re.Pattern[str] = re.compile(r"(?<![\w.@])@[A-Za-z0-9_]{2,}")
# What a line may not start like: a heading of the prompt (a Markdown one, whatever its words), a note of the loop or a
# turn of a conversation (``[system note]``, ``<system>``, ``System:``). The habits and the tool guidance stand in the
# prompt line by line, and a line like these would pass for a section of the prompt, or for what the system said.
_HEADING: re.Pattern[str] = re.compile(r"^[ \t>]*#{1,6}(?:[ \t]|$)", re.MULTILINE)
# A Markdown heading written with its underline: a line, and under it a line of ``=`` or of two dashes or more.
_UNDERLINED: re.Pattern[str] = re.compile(r"^(?=[^\n]*\S)[^\n]*+\n[ \t]{0,3}+(?:=++|-{2,}+)[ \t]*+$", re.MULTILINE)
# A line starts after a quote mark, a list marker (a bullet or a number) and the emphasis of a bold or code span.
_LINE: str = r"^[ \t>]*+(?:(?:[-*+•]|\d{1,3}[.)])[ \t]++)?+[*_`]*+[ \t]*+"
_SYSTEM_NOTE: re.Pattern[str] = re.compile(
    rf"{_LINE}[\[(<]\s*+/?+\s*+(?:system|developer|assistant|user|human|tool|dot|harness|note|instruction)s?\b"
    rf"|{_LINE}(?:system|developer|assistant|human|user|ai)(?:[ \t]++(?:note|message|prompt|reminder|instruction)s?)?+"
    r"[*_`]*+[ \t]*+:",
    re.IGNORECASE | re.MULTILINE,
)
# The tokens a model's chat format is made of (``<|im_start|>``, ``<|eot_id|>``, ``[INST]``, ``<<SYS>>``), anywhere in a
# text: they close a turn and open another.
_CHAT_TOKEN: re.Pattern[str] = re.compile(r"<\|[\w. -]{1,40}\|>|\[/?INST\]|<</?SYS>>", re.IGNORECASE)


def _ignorable(code: int) -> bool:
    """Whether the code point is a default-ignorable one (:data:`_IGNORABLE`)."""
    index: int = bisect.bisect_right(_IGNORABLE_STARTS, code) - 1
    return index >= 0 and code <= _IGNORABLE[index][1]


def _prints_nothing(char: str) -> bool:
    """Whether ``char`` is one that prints nothing: a control, a format character, a surrogate, a private-use or an
    unassigned code point, a line or paragraph separator, a default-ignorable code point or the Braille blank."""
    return unicodedata.category(char) in _UNPRINTABLE or _ignorable(ord(char)) or ord(char) in _BLANKS


def hidden_character(text: str) -> str | None:
    """The first character of ``text`` that prints nothing, as ``U+XXXX``, or ``None``: a control (a newline and a tab
    apart), a format character (zero-width, bidi, tag), a surrogate, a private-use or an unassigned code point, a line
    or paragraph separator, a default-ignorable code point (a variation selector, a filler, the combining grapheme
    joiner) or the Braille blank. Any of them could hide text from the owner who reads it."""
    return next((f"U+{ord(char):04X}" for char in text if char not in "\n\t" and _prints_nothing(char)), None)


def without_hidden(text: str) -> str:
    """``text`` as a reader sees it: without the characters :func:`hidden_character` finds, a newline and a tab apart.
    For a text that is shown and not refused (the words a third party wrote of itself, item 659)."""
    return "".join(char for char in text if char in "\n\t" or not _prints_nothing(char))


# The characters that are not ASCII, in runs: what a reading changes is among them, so the rest is never looked at one
# character at a time.
_NON_ASCII: re.Pattern[str] = re.compile(r"[^\x00-\x7f]+")


def _seen(run: re.Match[str]) -> str:
    """The run of non-ASCII characters without those that print nothing, and with a digit of another script
    (Arabic-Indic, Devanagari) put as the ASCII digit it is."""
    return "".join(
        str(unicodedata.decimal(char)) if char.isdecimal() else char
        for char in run.group()
        if not _ignorable(ord(char)) and ord(char) not in _BLANKS
    )


def _bare(run: re.Match[str]) -> str:
    """The run of non-ASCII characters decomposed (NFKD), without the combining marks."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", run.group()) if not unicodedata.category(char).startswith("M")
    )


def _folded(text: str) -> str:
    """``text`` (NFKC already) with the characters that print nothing left out, the letters that look like Latin ones
    put for them, the ideographic full stops read as dots and the digits of other scripts as ASCII ones."""
    return _NON_ASCII.sub(_seen, text).translate(_FOLDS)


def _unmarked(text: str) -> str:
    """``text`` (NFKC already) decomposed, with every combining mark taken off its letter: a mark that stands between
    the letters of a scheme, or after a dot, keeps a pattern from seeing the letters side by side."""
    return _NON_ASCII.sub(_bare, text)


def _fanged(text: str) -> str:
    """``text`` with the spellings that defang an address put back (:data:`_DEFANGED`)."""
    for pattern, replacement in _DEFANGED:
        text = pattern.sub(replacement, text)
    return text


def readings(text: str) -> tuple[str, ...]:
    """The forms of ``text`` every question of the gate is asked of, each once: as written, in NFKC, with the look-alike
    letters folded and what prints nothing left out, with the defanged spellings put back, and the last two again with
    the combining marks taken off (a text without a mark has no more forms for it)."""
    compatible: str = unicodedata.normalize("NFKC", text)
    folded: str = _folded(compatible)
    unmarked: str = _folded(_unmarked(compatible))
    return tuple(dict.fromkeys((text, compatible, folded, _fanged(folded), unmarked, _fanged(unmarked))))


def finds_secret(text: str, secrets: Sequence[str]) -> bool:
    """Whether ``text`` holds a secret in any reading: one of ``secrets`` (the values Dot holds), the value of a key
    that names a secret, or a key or a token of a known form (:func:`dotagent.core.text.redact_known`)."""
    return any(redact_known(reading, secrets) != reading for reading in readings(text))


def _public_address(reading: str) -> bool:
    """Whether ``reading`` holds a numeric address (``203.0.113.9``) that is not this machine's or a local network's."""
    for found in _ADDRESS.finditer(reading):
        try:
            address: ipaddress.IPv4Address = ipaddress.IPv4Address(found.group(0))
        except ipaddress.AddressValueError:
            continue
        if not any(address in network for network in _LOCAL_NETWORKS):
            return True
    return False


def _link_in(reading: str, naming: bool) -> bool:
    return (
        _SCHEME.search(reading) is not None
        or _SCHEMELESS.search(reading) is not None
        or _RELATIVE.search(reading) is not None
        or _HOST_ADDRESSED.search(reading) is not None
        or _public_address(reading)
        or (naming and _HOST_NAMED.search(reading) is not None)
    )


def finds_link(text: str, *, naming: bool = True) -> bool:
    """Whether ``text`` holds a link in any reading: an address with its scheme (``https://``, ``www.``), a ``data:``,
    ``javascript:``, ``mailto:`` or other address that acts without slashes, ``//host.tld``, a host with a path or a
    port after it (``evil.example/collect``), or a numeric address that is not a local one; and, with ``naming``, a host
    named bare on a known top-level domain (``evil.example``; on an ambiguous one such as ``.md`` or ``.info`` only
    with a third label). ``naming=False`` leaves the bare host out: a lesson and the description of a skill may name a
    site."""
    return any(_link_in(reading, naming) for reading in readings(text))


def finds_email(text: str) -> bool:
    """Whether ``text`` holds an e-mail address in any reading."""
    return any(_EMAIL.search(reading) is not None for reading in readings(text))


def _collapsed(path: str) -> str:
    """``path`` with its dot segments taken out: ``/Users/Shared/../ada`` is ``/Users/ada``, and ``~/../ada`` (the
    parent of the owner's own folder, which is ``/Users``) too."""
    if ".." not in path:
        return path
    path = _CURRENT_SEGMENT.sub("", _HOME_PARENT.sub("/Users", path))
    while (shorter := _DOT_SEGMENT.sub("", path, count=1)) != path:
        path = shorter
    return path


def finds_home_path(text: str) -> bool:
    """Whether ``text`` holds a path in somebody's home folder in any reading: ``/Users/ada``, ``/home/ada``, in any
    case and with any slashes (a backslash too), with the dot segments taken out (``/Users/Shared/../ada``), or
    ``~ada/``. The owner's own ``~/`` is not one."""
    for reading in readings(text):
        slashed: str = reading.replace("\\", "/")
        if any(_HOME_PATH.search(form) is not None for form in (reading, slashed, _collapsed(slashed))):
            return True
    return False


def finds_handle(text: str) -> bool:
    """Whether ``text`` holds an account name (``@ada``) in any reading."""
    return any(_HANDLE.search(reading) is not None for reading in readings(text))


def imitation(text: str) -> str | None:
    """Why a line of ``text`` imitates the prompt, or ``None``: it starts like a heading (``# Trust and safety``, or a
    line with ``===`` under it) or like a note of the system or a turn of a conversation (``[system note]``,
    ``<system>``, ``System:`` or ``- **User:**``), or it holds a token of a chat format (``<|im_start|>``, ``[INST]``,
    ``<<SYS>>``)."""
    for reading in readings(text):
        if _HEADING.search(reading) is not None or _UNDERLINED.search(reading) is not None:
            return "a line that imitates a heading of the prompt"
        if _SYSTEM_NOTE.search(reading) is not None or _CHAT_TOKEN.search(reading) is not None:
            return "a line that imitates a note of the system"
    return None


def wears(text: str, mark: str) -> bool:
    """Whether ``text`` holds ``mark`` (the owner's ``(you said)``) in any reading, whatever its case and spacing."""
    wanted: str = "".join(mark.casefold().split())
    return any(wanted in "".join(reading.casefold().split()) for reading in readings(text))


def gate(text: str, secrets: Sequence[str], *, addresses: bool = True) -> str | None:
    """Why ``text`` may not be learned, as a phrase, or ``None`` when it may: a character that prints nothing, a secret
    (one of ``secrets``, the values Dot holds, or a key of a known form), an e-mail address, a link, a path in a home
    folder, an account name, the owner's mark (:data:`OWNER_MARK`: only the owner's own words wear it), or a line that
    imitates the prompt, in that order, each asked of every reading
    (:func:`readings`). ``addresses=False`` leaves out the link and the account name: a lesson of a run that read
    nothing others wrote may name a site, and one of a run that did is asked about both apart."""
    hidden: str | None = hidden_character(text)
    if hidden is not None:
        return f"a character that does not print ({hidden})"
    if finds_secret(text, secrets):
        return "a secret"
    # The e-mail address is asked before the link: a fullwidth at sign leaves the host of an address looking like one.
    if finds_email(text):
        return "an e-mail address"
    if addresses and finds_link(text):
        return "a link"
    if finds_home_path(text):
        return "a path in a home folder"
    if addresses and finds_handle(text):
        return "an account name"
    if wears(text, OWNER_MARK):
        return "a line that wears the owner's mark"
    return imitation(text)
