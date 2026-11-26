"""The non-security task kinds used at the start of a run or delegated part."""

import re
import unicodedata

from dotagent.config import TaskKind

_CODE = re.compile(
    r"^(?:please )?(?:implement|fix|refactor|add|update|remove|rename|migrate|build|change|ship|"
    r"merge|commit|patch|write|create)\b"
    r"[^\n.!?]{0,160}\b(?:code|repo(?:sitory)?|tests?|functions?|class|module|component|endpoint|schema|branch|"
    r"commits?|pull request|pr|ci|github|bug|typescript|python|swift|rust)\b"
    r"|^[^\n.!?]{0,80}\b(?:github|repo|pr|kod|test|modül|bileşen|hata|branch)(?:[’'][a-zçğıöşü]+|[a-zçğıöşü]{0,5})"
    r"\b[^\n.!?]{0,100}\b(?:ekle|düzelt|değiştir|güncelle|kaldır|yaz|uygula|birleştir)\b"
)
_WRITING = re.compile(
    r"^(?:please )?(?:write|draft|compose|rewrite|edit|translate|proofread|prepare|update)\b"
    r"[^\n.!?]{0,160}\b(?:copy|docs?|documentation|readme|guide|e-?mail|message|report|article|proposal|letter|script|post)\b"
    r"|^[^\n.!?]{0,80}\b(?:metin|belge|rapor|mesaj|(?:e-?)?posta|makale|taslak)[a-zçğıöşü]{0,5}"
    r"\b[^\n.!?]{0,100}\b(?:yaz|hazırla|düzenle|çevir)\b"
)
_DEEP = re.compile(
    r"^(?:please )?(?:deep dive|analy[sz]e|investigate|diagnose|design|evaluate|compare|research|debug|"
    r"trace the root cause|ayrıntılı incele|analiz et|araştır|karşılaştır)\b"
    r"|^[^\n.!?]{0,160}\b(?:kök neden\w* (?:bul|incele)|ayrıntılı incele|derinlemesine araştır|detaylı analiz et)\b"
)
_QUICK = re.compile(
    r"^(?:please )?(?:look up|list|count|show me|check whether|check if|tell me (?:if|whether)|what (?:is|are)|"
    r"how many|find the (?:link|url|date|time)|get the (?:link|url|date|time)|listele|sayısını bul|kaç tane)\b"
    r"|^[^\n.!?]{0,120}\b(?:listele|sayısını bul|kaç tane|linkini bul|tarihini bul)\b"
)
_MULTIPLE = re.compile(r"[\n;]|\b(?:and then|also|after that|then|ardından|sonra|ayrıca)\b")


def task_kind(goal: str) -> TaskKind | None:
    """Classify a clearly stated task from its opening. Ambiguous goals keep their normal model."""
    text: str = unicodedata.normalize("NFKC", goal.lstrip(" \t\n#*-")).lower().replace("i\u0307", "i")
    if _CODE.search(text):
        return "code"
    if _WRITING.search(text):
        return "writing"
    if _DEEP.search(text):
        return "deep"
    if len(text.split()) <= 24 and _QUICK.search(text) and not _MULTIPLE.search(text):
        return "quick"
    return None
