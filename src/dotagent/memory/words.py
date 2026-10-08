"""The words of a query that can find a note: its topic words, without the function words of English and Turkish."""

import re

_WORD: re.Pattern[str] = re.compile(r"\w{2,}")
_COMBINING_DOT: str = "\u0307"  # Python lowercases the Turkish İ to "i" followed by this mark
_MAX_TERMS: int = 24

# "don't" arrives as "don" and "t": the pieces of contractions are listed too.
_STOPWORDS: frozenset[str] = frozenset(
    """
    about above after again all also am an and any are as at be because been before being below between both but by
    can could did didn do does doesn doing don down during each few for from further had hadn has hasn have haven
    having he her here hers him his how if in into is isn it its just ll me more most my no nor not now of off on once
    only or other our ours out over own re same she should shouldn so some such than that the their theirs them then
    there these they this those through to too under until up us ve very was wasn we were weren what when where which
    while who whom why will with won would wouldn you your yours
    acaba ama ancak artık bana bazı belki ben beni benim bir biri birkaç birçok biz bizi bizim bu bunu bunun bunlar
    burada çok çünkü da daha de değil diye eğer en gibi hangi hem hep hepsi her hiç için ile ise kadar ki kim mi mı mu
    mü ne neden nerede nasıl niçin olan olarak oldu olduğu olmak onu onun onlar sadece sana sanki sen seni siz sizi şey
    şu sonra tüm ve veya ya yani yoksa
    """.split()
)


def lowercase(text: str) -> str:
    """``text`` in lowercase; the Turkish İ becomes a plain i instead of an i followed by a combining dot."""
    return text.lower().replace(_COMBINING_DOT, "")


def search_terms(text: str) -> tuple[str, ...]:
    """The distinct words of ``text`` that can tell notes apart, lowercase, in the order they occur."""
    topic: dict[str, None] = {word: None for word in _WORD.findall(lowercase(text)) if word not in _STOPWORDS}
    return tuple(topic)[:_MAX_TERMS]
