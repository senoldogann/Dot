"""Routes for the composer's microphone: on a phone (item 626) whether this Mac can write a recording down, and writing
one down; in Dot.app (item 636) System Settings, shown where the owner allows Dot the microphone.

An iPhone's Safari hears the first dictation of a page and at times none after it, so the page there records the voice
itself and sends it here; the Mac writes it down with macOS's own dictation model, on the device
(:mod:`dotagent.voice`). The routes are behind the API token like every other: a phone paired with Settings › Phone
reaches the first two as it reaches the composer. The recording and the words are never logged and never kept.

macOS refuses the microphone to a Dot.app, and the page there asks the Mac to show System Settings at the pane where
Dot is switched on, from this Mac's own address alone (a phone has no use for it). It is shown only for a Dot.app that
asks macOS for the microphone: one built before item 594 is given none whatever System Settings shows
(:mod:`dotagent.voice.desktopapp`), and the answer says so. No setting is changed: the owner switches Dot on.
"""

import asyncio
import base64
import binascii

from fastapi import APIRouter, Request

from dotagent.core.errors import InvalidValueError, SignInError
from dotagent.core.logs import get_logger
from dotagent.core.paths import voice_dir
from dotagent.server.routes_accounts import on_this_mac
from dotagent.server.views import RecordingUpload, TranscriptView, VoiceAllowRequest, VoiceAllowView
from dotagent.voice.desktopapp import AppState, DesktopApp, Opener, open_url, settings_url
from dotagent.voice.helper import Ability, Transcriber
from dotagent.voice.wav import Recording, read_wav

log = get_logger(__name__)


def voice_router(desktop_app: DesktopApp | None = None, opener: Opener = open_url) -> APIRouter:
    """The voice routes. ``desktop_app`` reads the Dot.app that runs and ``opener`` shows System Settings; a test gives
    its own of each."""
    router: APIRouter = APIRouter(prefix="/api")
    transcriber: Transcriber = Transcriber(voice_dir())
    app: DesktopApp = DesktopApp() if desktop_app is None else desktop_app

    @router.get("/voice")
    async def voice() -> Ability:
        """Whether this Mac can write a recording down and, when it cannot, why in plain words and what to do. A page
        asks as it starts, in the background; when the Mac can, the program that does it is built now in the background,
        so that the first recording does not wait for the compiler. What the Mac has to build with is asked of the Mac
        once and remembered (``Transcriber``), not at each ask."""
        ability: Ability = await transcriber.ability()
        if ability.transcribe:
            transcriber.warm()
        return ability

    @router.post("/voice/transcribe")
    async def transcribe(request: RecordingUpload) -> TranscriptView:
        """The words of a recording (a 16 kHz mono 16-bit WAV of five minutes at the most) in ``language``, written down
        by this Mac. 422 with what to do when the bytes are no such recording, the language is not a tag, or the Mac
        cannot write it down (``context.code``: ``unavailable``, ``unsupported-language``, ``no-audio``, ``timeout``,
        ``failed``)."""
        try:
            data: bytes = base64.b64decode(request.data_b64, validate=True)
        except binascii.Error as error:
            raise InvalidValueError("the recording is not valid base64", {}) from error
        recording: Recording = read_wav(data)
        return TranscriptView(text=await transcriber.transcribe(recording, request.language))

    @router.post("/voice/allow")
    async def allow(request: Request, body: VoiceAllowRequest) -> VoiceAllowView:
        """Show System Settings at the pane where Dot is allowed the microphone or speech recognition (item 636), for
        the Dot.app that was refused it, unless that Dot.app was built before it asked macOS for either: then nothing is
        shown and the answer says ``old``. Refused (422) from any address but this Mac's own."""
        if not on_this_mac(request):
            raise SignInError(
                "System Settings opens on the Mac itself: ask for it at the Mac's own address, not from another device",
                {},
            )
        state: AppState = await asyncio.to_thread(app.state)
        if state == "old":
            log.info("voice_allow_old_app", pane=body.pane)
            return VoiceAllowView(opened=False, app=state)
        await asyncio.to_thread(opener, settings_url(body.pane))
        log.info("voice_allow_opened", pane=body.pane, app=state)
        return VoiceAllowView(opened=True, app=state)

    return router
