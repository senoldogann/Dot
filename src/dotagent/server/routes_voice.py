"""Routes for the composer's microphone on a phone (item 626): whether this Mac can write a recording down, and writing
one down.

An iPhone's Safari hears the first dictation of a page and at times none after it, so the page there records the voice
itself and sends it here; the Mac writes it down with macOS's own dictation model, on the device
(:mod:`dotagent.voice`). Both routes are behind the API token like every other: a phone paired with Settings › Phone
reaches them as it reaches the composer. The recording and the words are never logged and never kept.
"""

import base64
import binascii

from fastapi import APIRouter

from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import voice_dir
from dotagent.server.views import RecordingUpload, TranscriptView
from dotagent.voice.helper import Ability, Transcriber
from dotagent.voice.wav import Recording, read_wav


def voice_router() -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    transcriber: Transcriber = Transcriber(voice_dir())

    @router.get("/voice")
    async def voice() -> Ability:
        """Whether this Mac can write a recording down and, when it cannot, why in plain words and what to do. A page
        asks once as it starts; when the Mac can, the program that does it is built now in the background, so that the
        first recording does not wait for the compiler."""
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

    return router
