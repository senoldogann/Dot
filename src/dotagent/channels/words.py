"""The words Dot sends on a channel of its own, in the owner's language (item 805): every line a channel says that no
model wrote, from the note that a run was queued to the lines of progress, in English and in Turkish.

``[channels] language`` picks them: ``auto`` (the default) speaks the language of the owner's last message, told by its
letters and its commonest words (:func:`language_of`); ``en`` and ``tr`` hold one. A message whose language cannot be
told (a link, a number, ``ok``) keeps the one before it, and the language is kept across restarts, so that what Dot
tells on its own (the morning brief) speaks it too. An answer is the model's and in whatever language it wrote; these
are the words around it. What a run is told (the note that an attachment was not read) stays in English: it is for
the model, not the owner."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

type Language = Literal["en", "tr"]
type LanguageSetting = Literal["auto", "en", "tr"]

LANGUAGES: tuple[Language, ...] = ("en", "tr")
DEFAULT_LANGUAGE: Language = "en"

# Letters only Turkish of the languages the owner writes has; and the commonest short words of each language, which a
# message of a few words is told by. A word counts once however often it comes.
_TURKISH_LETTERS: re.Pattern[str] = re.compile(r"[ğĞıİşŞ]")
_WORD: re.Pattern[str] = re.compile(r"[^\W\d_]+", re.UNICODE)
_TURKISH_WORDS: frozenset[str] = frozenset(
    {
        "ve",
        "bir",
        "bu",
        "şu",
        "ne",
        "mi",
        "mı",
        "mu",
        "mü",
        "için",
        "ile",
        "de",
        "da",
        "ki",
        "ama",
        "çok",
        "var",
        "yok",
        "nasıl",
        "neden",
        "nerede",
        "bana",
        "beni",
        "benim",
        "sen",
        "sana",
        "lütfen",
        "evet",
        "hayır",
        "tamam",
        "yap",
        "yapar",
        "misin",
        "musun",
        "merhaba",
        "teşekkürler",
        "şimdi",
        "bugün",
        "yarın",
        "gibi",
        "daha",
        "olarak",
        "olan",
        "kadar",
        "her",
        "hangi",
        "abi",
        "dur",
        "durdur",
    }
)
_ENGLISH_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "is",
        "are",
        "to",
        "of",
        "for",
        "with",
        "what",
        "how",
        "why",
        "where",
        "please",
        "can",
        "could",
        "you",
        "my",
        "me",
        "this",
        "that",
        "it",
        "do",
        "does",
        "find",
        "check",
        "show",
        "tell",
        "today",
        "tomorrow",
        "yes",
        "thanks",
        "hello",
        "stop",
    }
)


def language_of(text: str) -> Language | None:
    """The language of ``text``, the owner's message: Turkish when it has a letter only Turkish has (ğ, ı, İ, ş) or more
    of its common words than English, English when it has more of those; ``None`` when neither tells (a link, a number,
    a single word of neither list)."""
    if _TURKISH_LETTERS.search(text):
        return "tr"
    words: set[str] = {word.casefold() for word in _WORD.findall(text)}
    turkish: int = len(words & _TURKISH_WORDS)
    english: int = len(words & _ENGLISH_WORDS)
    if turkish > english:
        return "tr"
    if english > turkish:
        return "en"
    return None


def setting_language(setting: LanguageSetting, heard: Language | None) -> Language:
    """The language Dot speaks on a channel: the one ``setting`` holds, or with ``auto`` the one the owner's messages
    were last told in (``heard``), English before any was."""
    if setting != "auto":
        return setting
    return heard or DEFAULT_LANGUAGE


@dataclass(frozen=True)
class Words:
    """The lines of one language. A field with ``{…}`` in it is a template, filled by the method beside it."""

    # Intake (item 572, 611).
    too_old: str
    attachment_only: str
    files_coming: str
    unread_answer: str
    flood: str
    too_long: str
    by_a_run: str
    scheduled: str
    pairing_code: str
    # The router.
    stopped: str
    queued: str
    could_not_start: str
    failed_message: str
    no_run: str
    stopping: str
    done: str
    run_failed: str
    run_stopped: str
    run_ended: str
    has_question: str
    needs_help: str
    waits_approval: str
    and_more: str
    answer_it: str
    answer_them: str
    on_your_mac: str
    not_here: str
    told_more: str
    not_sent: str
    # The cut of an answer (item 611).
    rest_in_dot: str
    rest_on_mac: str
    # The pictures of an answer (item 617).
    left_out: str
    more: str
    reasons: Mapping[str, str]
    # Progress (item 618).
    working_for: str
    earlier_step: str
    earlier_steps: str
    seconds: str
    minutes: str
    hours: str
    steps: Mapping[str, str]

    def fill(self, template: str, **values: object) -> str:
        return template.format(**values)

    def elapsed(self, seconds: float) -> str:
        """A time of work in words: ``15 s``, ``2 min 5 s``, ``1 h 3 min`` (``15 sn``, ``2 dk 5 sn``, ``1 sa 3 dk``)."""
        whole: int = max(0, int(seconds))
        if whole < 60:
            return f"{whole} {self.seconds}"
        minutes, rest = divmod(whole, 60)
        if minutes < 60:
            return f"{minutes} {self.minutes} {rest} {self.seconds}" if rest else f"{minutes} {self.minutes}"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} {self.hours} {minutes} {self.minutes}" if minutes else f"{hours} {self.hours}"

    def step(self, key: str, detail: str = "") -> str:
        """A step of progress: the phrase ``key`` names (one of :data:`STEP_KEYS`), with ``detail`` (a host, a file's
        name, a query) where its template takes one."""
        return self.steps[key].format(detail=detail)

    def reason(self, why: str) -> str:
        """Why a picture was left out, in this language: ``why`` is the English reason the finder gives."""
        return self.reasons.get(why, why)


# The steps of progress a phrase is kept for: ``{detail}`` is what the step was given (a file's name, a host, a query).
STEP_KEYS: tuple[str, ...] = (
    "ran_command",
    "read_file",
    "read_a_file",
    "wrote_file",
    "wrote_a_file",
    "edited_file",
    "edited_a_file",
    "searched_web",
    "searched_web_for",
    "read_page",
    "read_page_on",
    "crawled_site",
    "crawled_site_on",
    "opened_page",
    "opened_page_on",
    "handed_work",
    "handed_work_for",
    "used_skill",
    "used_skill_named",
    "used_browser",
    "used_desktop",
    "used_tool",
    "list_dir",
    "find_files",
    "search_files",
    "apple_script",
    "mail_read",
    "mail_search",
    "calendar_events",
    "memory_save",
    "memory_update",
    "memory_search",
    "memory_forget",
    "write_notes",
    "schedule_create",
    "schedule_list",
    "schedule_delete",
    "notify",
    "show_page",
    "draft_message",
    "consult",
    "message_agent",
    "create_agent",
    "code_fetch",
    "browser_snapshot",
)

ENGLISH: Words = Words(
    too_old=(
        "That message came in more than 15 minutes ago, while Dot was not reading, so Dot did not run it. Send it "
        "again if you still want it."
    ),
    attachment_only=(
        "Dot could not read that: it reads pictures, PDFs, text and Office files, not voice messages or videos yet."
    ),
    files_coming="That file had not come down to this Mac yet. Send it again in a moment.",
    unread_answer="Dot did not read the picture or file that came with your message, so this answers your words only.",
    flood="That is more than 30 messages in a minute. Dot reads no more until it is quieter.",
    too_long="That message is {chars} characters and Dot reads {limit} at most. Shorten it or send it in parts.",
    by_a_run=(
        "Dot did not run that: a run of Dot sent a message at that moment, and Dot cannot tell its message from yours. "
        "Send it again."
    ),
    scheduled=(
        "Dot did not run that: it is a scheduled message, and Dot cannot tell when it was written. Send it now, not "
        "scheduled."
    ),
    pairing_code=(
        "Dot's pairing code is {code}. Type it in Settings › Channels on your Mac. If you did not ask for it, ignore "
        "this message."
    ),
    stopped=(
        "Dot is stopped: the emergency stop is engaged, so nothing was started. Release it in Dot (the banner at the "
        "top of any page has the button), then send the message again."
    ),
    queued="Queued: the run at work in this chat goes first. Send /stop to stop it.",
    could_not_start="Dot could not start on that: {error}.",
    failed_message="Dot failed on that message. The log has what went wrong.",
    no_run="No run is at work in this chat.",
    stopping="Stopping the run.",
    done="Done.",
    run_failed="The run failed.",
    run_stopped="The run was stopped.",
    run_ended="The run ended.",
    has_question="Dot has a question",
    needs_help="Dot needs your help",
    waits_approval="Dot waits for your approval",
    and_more=" (and {count} more)",
    answer_it="Answer it in Dot",
    answer_them="Answer them in Dot",
    on_your_mac="{where} on your Mac, not here.",
    not_here="{where}, not here: {link}",
    told_more="{title}: and {count} more",
    not_sent="Dot could not send {names}. The log has why.",
    rest_in_dot="\n\n… The rest of the answer is in Dot: ",
    rest_on_mac="\n\n… The rest of the answer is in Dot on your Mac.",
    left_out="Left out: {parts}.",
    more="{count} more",
    reasons={},
    working_for="Working for {elapsed}:",
    earlier_step="… {count} earlier step",
    earlier_steps="… {count} earlier steps",
    seconds="s",
    minutes="min",
    hours="h",
    steps={
        "ran_command": "ran a command",
        "read_file": "read {detail}",
        "read_a_file": "read a file",
        "wrote_file": "wrote {detail}",
        "wrote_a_file": "wrote a file",
        "edited_file": "edited {detail}",
        "edited_a_file": "edited a file",
        "searched_web": "searched the web",
        "searched_web_for": "searched the web: {detail}",
        "read_page": "read a page",
        "read_page_on": "read a page: {detail}",
        "crawled_site": "crawled a site",
        "crawled_site_on": "crawled a site: {detail}",
        "opened_page": "opened a page",
        "opened_page_on": "opened a page: {detail}",
        "handed_work": "handed work to a helper",
        "handed_work_for": "handed work to a helper: {detail}",
        "used_skill": "used a skill",
        "used_skill_named": "used the skill {detail}",
        "used_browser": "used the browser",
        "used_desktop": "used the desktop",
        "used_tool": "used {detail}",
        "list_dir": "looked in a folder",
        "find_files": "looked for files",
        "search_files": "searched files",
        "apple_script": "ran an AppleScript",
        "mail_read": "read mail",
        "mail_search": "searched mail",
        "calendar_events": "looked at the calendar",
        "memory_save": "kept a note",
        "memory_update": "updated a note",
        "memory_search": "searched the notes",
        "memory_forget": "forgot a note",
        "write_notes": "wrote notes",
        "schedule_create": "added a schedule",
        "schedule_list": "looked at the schedules",
        "schedule_delete": "deleted a schedule",
        "notify": "notified you",
        "show_page": "showed a page",
        "draft_message": "drafted a message",
        "consult": "asked a helper",
        "message_agent": "wrote to an agent",
        "create_agent": "made an agent",
        "code_fetch": "fetched a code",
        "browser_snapshot": "looked at a page",
    },
)

TURKISH: Words = Words(
    too_old=(
        "Bu mesaj 15 dakikadan önce geldi, Dot o sırada okumuyordu; bu yüzden çalıştırmadı. Hâlâ istiyorsan yeniden "
        "gönder."
    ),
    attachment_only=(
        "Bunu okuyamadım: resim, PDF, metin ve Office dosyalarını okuyorum; sesli mesajları ve videoları henüz değil."
    ),
    files_coming="Bu dosya henüz bu Mac'e inmedi. Birazdan yeniden gönder.",
    unread_answer="Mesajınla gelen resmi ya da dosyayı okuyamadım; bu cevap yalnızca yazdıklarına.",
    flood="Bir dakikada 30'dan fazla mesaj geldi. Ortalık sakinleşene kadar Dot yenilerini okumuyor.",
    too_long="Bu mesaj {chars} karakter; Dot en çok {limit} karakter okuyor. Kısalt ya da parça parça gönder.",
    by_a_run=(
        "Bunu çalıştırmadım: aynı anda Dot'un bir görevi de mesaj gönderdi ve onunkini seninkinden ayıramıyorum. "
        "Yeniden gönder."
    ),
    scheduled=(
        "Bunu çalıştırmadım: zamanlanmış bir mesaj ve ne zaman yazıldığını bilemiyorum. Zamanlamadan, şimdi gönder."
    ),
    pairing_code=(
        "Dot eşleştirme kodu: {code}. Mac'inde Ayarlar › Kanallar'a yaz. Bunu sen istemediysen bu mesajı yok say."
    ),
    stopped=(
        "Dot durduruldu: acil durdurma açık, bu yüzden hiçbir şey başlatılmadı. Dot'ta kaldır (her sayfanın üstündeki "
        "şeritte düğmesi var), sonra mesajı yeniden gönder."
    ),
    queued="Sıraya alındı: önce bu sohbette çalışan görev bitecek. Durdurmak için /stop yaz.",
    could_not_start="Buna başlayamadım: {error}.",
    failed_message="Bu mesajda bir hata oldu. Ne olduğu günlükte yazıyor.",
    no_run="Bu sohbette çalışan bir görev yok.",
    stopping="Görevi durduruyorum.",
    done="Tamam.",
    run_failed="Görev başarısız oldu.",
    run_stopped="Görev durduruldu.",
    run_ended="Görev bitti.",
    has_question="Dot'un bir sorusu var",
    needs_help="Dot'un yardımına ihtiyacı var",
    waits_approval="Dot onayını bekliyor",
    and_more=" (ve {count} tane daha)",
    answer_it="Dot'ta cevapla",
    answer_them="Hepsini Dot'ta cevapla",
    on_your_mac="{where}, Mac'inde; burada değil.",
    not_here="{where}, burada değil: {link}",
    told_more="{title}: ve {count} tane daha",
    not_sent="{names} gönderilemedi. Nedeni günlükte yazıyor.",
    rest_in_dot="\n\n… Cevabın devamı Dot'ta: ",
    rest_on_mac="\n\n… Cevabın devamı Mac'indeki Dot'ta.",
    left_out="Gönderilmeyenler: {parts}.",
    more="{count} tane daha",
    reasons={
        "over 20 MB": "20 MB'tan büyük",
        "4 at most in a message": "bir mesajda en çok 4 tane",
        "not a PNG, JPEG, GIF, HEIC or WebP": "PNG, JPEG, GIF, HEIC ya da WebP değil",
        "in a place Dot does not send pictures from": "Dot'un resim göndermediği bir yerde",
        "cannot be read": "okunamıyor",
        "it is gone": "artık yok",
    },
    working_for="Çalışıyorum ({elapsed}):",
    earlier_step="… önceki {count} adım",
    earlier_steps="… önceki {count} adım",
    seconds="sn",
    minutes="dk",
    hours="sa",
    steps={
        "ran_command": "bir komut çalıştırdım",
        "read_file": "{detail} dosyasını okudum",
        "read_a_file": "bir dosya okudum",
        "wrote_file": "{detail} dosyasını yazdım",
        "wrote_a_file": "bir dosya yazdım",
        "edited_file": "{detail} dosyasını düzenledim",
        "edited_a_file": "bir dosyayı düzenledim",
        "searched_web": "webde aradım",
        "searched_web_for": "webde aradım: {detail}",
        "read_page": "bir sayfa okudum",
        "read_page_on": "bir sayfa okudum: {detail}",
        "crawled_site": "bir siteyi taradım",
        "crawled_site_on": "bir siteyi taradım: {detail}",
        "opened_page": "bir sayfa açtım",
        "opened_page_on": "bir sayfa açtım: {detail}",
        "handed_work": "işi bir yardımcıya verdim",
        "handed_work_for": "işi bir yardımcıya verdim: {detail}",
        "used_skill": "bir beceri kullandım",
        "used_skill_named": "{detail} becerisini kullandım",
        "used_browser": "tarayıcıyı kullandım",
        "used_desktop": "masaüstünü kullandım",
        "used_tool": "{detail} kullandım",
        "list_dir": "bir klasöre baktım",
        "find_files": "dosya aradım",
        "search_files": "dosyaların içinde aradım",
        "apple_script": "bir AppleScript çalıştırdım",
        "mail_read": "e-posta okudum",
        "mail_search": "e-postalarda aradım",
        "calendar_events": "takvime baktım",
        "memory_save": "bir not aldım",
        "memory_update": "bir notu güncelledim",
        "memory_search": "notlarda aradım",
        "memory_forget": "bir notu sildim",
        "write_notes": "notlar yazdım",
        "schedule_create": "bir zamanlama ekledim",
        "schedule_list": "zamanlamalara baktım",
        "schedule_delete": "bir zamanlamayı sildim",
        "notify": "sana bildirim gönderdim",
        "show_page": "bir sayfa gösterdim",
        "draft_message": "bir mesaj taslağı yazdım",
        "consult": "bir yardımcıya danıştım",
        "message_agent": "bir ajana yazdım",
        "create_agent": "bir ajan oluşturdum",
        "code_fetch": "bir kod aldım",
        "browser_snapshot": "bir sayfaya baktım",
    },
)

WORDS: Mapping[Language, Words] = {"en": ENGLISH, "tr": TURKISH}

# Where the language the owner's messages were last told in is kept, across restarts.
LANGUAGE_KEY: str = "channels.language"


class KeptValues(Protocol):
    async def kv_get(self, key: str) -> str | None: ...

    async def kv_set(self, key: str, value: str) -> None: ...


class SpokenLanguage:
    """The language Dot speaks on its channels (item 805), one for all of them: ``setting`` is ``[channels] language``
    as it is now, and with ``auto`` the language the owner's messages were last told in, which :meth:`hear` follows and
    keeps in ``store``."""

    def __init__(self, store: KeptValues, setting: Callable[[], LanguageSetting]) -> None:
        self._store: KeptValues = store
        self._setting: Callable[[], LanguageSetting] = setting
        self._heard: Language | None = None

    async def load(self) -> None:
        kept: str | None = await self._store.kv_get(LANGUAGE_KEY)
        self._heard = next((language for language in LANGUAGES if language == kept), None)

    async def hear(self, text: str) -> None:
        """The owner wrote ``text``: its language, when it can be told, is the one Dot speaks from now with ``auto``."""
        told: Language | None = language_of(text)
        if told is not None and told != self._heard:
            self._heard = told
            await self._store.kv_set(LANGUAGE_KEY, told)

    @property
    def language(self) -> Language:
        return setting_language(self._setting(), self._heard)

    def words(self) -> Words:
        return WORDS[self.language]
