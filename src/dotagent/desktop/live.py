"""The Mac's screen as live H.264 video (Watch): ScreenCaptureKit captures the main display, the Mac's hardware encoder
(VideoToolbox) compresses it, and each viewer reads the encoded frames as they come.

One capture serves every viewer: it starts with the first and stops when the last one leaves, so nothing is captured
while nobody watches (macOS shows its screen-recording mark meanwhile). A capture that macOS stops, or whose encoder
fails (a Mac that slept), ends its viewers, whose page says so and offers to try again; a viewer that goes while its
capture is still starting leaves it to be stopped once it has started. A viewer that falls behind loses frames until
the next key frame, which is then asked of the encoder, so a slow phone sees the screen as it is now rather than a
growing delay. Measured on the owner's MacBook (1920 x 1248 at 60 fps): 57 frames a second, about 15 ms to encode a
frame, 8% of one core and 1 to 1.5 Mbit/s for an ordinary desktop.

A frame on the wire is a header of 13 bytes, its kind (``KEY`` or ``DELTA``), the length of what follows and its time in
microseconds, all big-endian, then the frame in AVCC form (NAL units, each after its 4-byte length). A key frame's
payload starts with the decoder's configuration (the ``avcC`` record, after its 2-byte length), which is all a WebCodecs
``VideoDecoder`` needs.
"""

import asyncio
import struct
import threading
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass

import CoreMedia as CM
import objc
import Quartz
import ScreenCaptureKit as SCK
import VideoToolbox as VT
from Foundation import NSObject

from dotagent.core.errors import DesktopError, DesktopPermissionError
from dotagent.core.logs import get_logger

log = get_logger(__name__)

KEY: int = 1
DELTA: int = 2
_HEADER: struct.Struct = struct.Struct(">BIQ")
_CONFIG_LENGTH: struct.Struct = struct.Struct(">H")
_USER_DECLINED: int = -3801  # SCStreamErrorUserDeclined: Screen Recording is not granted
_PIXEL_FORMAT_420V: int = 875704438  # '420v', what the hardware encoder takes without a conversion
_ANSWER_S: float = 5.0
# Frames a viewer may have waiting before it is behind: half a second at 60 frames a second.
_BACKLOG: int = 30

# PyObjC knows the session only as an opaque pointer, which VideoToolbox's property calls refuse (kVTParameterErr):
# registered as the Core Foundation type it is, the session is passed as one.
objc.registerCFSignature(
    "VTCompressionSessionRef", b"^{OpaqueVTCompressionSession=}", VT.VTCompressionSessionGetTypeID()
)


@dataclass(frozen=True)
class LiveSettings:
    """What the capture delivers: the longest side of a frame in pixels, frames a second, the encoder's average bit rate
    and the longest time between two key frames, so that a frame lost on the way, or dropped for a viewer that fell
    behind, heals by itself within it."""

    max_edge: int
    fps: int
    bitrate: int
    key_every_s: int


def frame_size(points: tuple[float, float], pixels: tuple[int, int], max_edge: int) -> tuple[int, int]:
    """The size of a frame of a display of ``points`` (and ``pixels`` natively): at most ``max_edge`` on its longest
    side and never more than the display's own pixels, in even numbers, as H.264's 4:2:0 needs."""
    native: float = pixels[0] / points[0]
    scale: float = min(native, max_edge / max(points))
    return max(2, int(points[0] * scale) // 2 * 2), max(2, int(points[1] * scale) // 2 * 2)


def avc_message(kind: int, micros: int, config: bytes, frame: bytes) -> bytes:
    """One frame as it goes on the wire (module docstring); ``config`` is empty for a ``DELTA``."""
    payload: bytes = _CONFIG_LENGTH.pack(len(config)) + config + frame if kind == KEY else frame
    return _HEADER.pack(kind, len(payload), micros) + payload


# Where an encoded frame goes (the capture it is of, is it a key frame, its message), and what hears that a capture
# failed and why (macOS stopped it, its encoder failed); both are called on the threads of ScreenCaptureKit and
# VideoToolbox.
type FrameSink = Callable[["_Capture", bool, bytes], None]
type FailureSink = Callable[["_Capture", str], None]


class Viewer:
    """One reader of the stream: its frames waiting, and whether it waits for a key frame."""

    def __init__(self) -> None:
        self.frames: asyncio.Queue[bytes | None] = asyncio.Queue(_BACKLOG)
        self.needs_key: bool = True


class _Capture:
    """A capture of the main display and its encoder: made, then started once, then closed once (:func:`_open`)."""

    def __init__(self, settings: LiveSettings, on_frame: FrameSink, on_failure: FailureSink) -> None:
        self._on_frame: FrameSink = on_frame
        self._on_failure: FailureSink = on_failure
        self._key_wanted: threading.Event = threading.Event()
        # Set once the capture failed (told once) and once macOS stopped its stream (which is then not stopped again).
        self.failed: threading.Event = threading.Event()
        self._stream_stopped: threading.Event = threading.Event()
        # Start and close one at a time: a close waits for a start still going on, and a stream that started is stopped.
        self._lifecycle: threading.Lock = threading.Lock()
        self._started: bool = False
        self._closed: bool = False
        display: object = _main_display()
        width, height = frame_size(
            (float(display.width()), float(display.height())),  # type: ignore[attr-defined]  # an SCDisplay
            _display_pixels(int(display.displayID())),  # type: ignore[attr-defined]
            settings.max_edge,
        )
        self._session: object = _encoder(width, height, settings)
        config = SCK.SCStreamConfiguration.alloc().init()
        config.setWidth_(width)
        config.setHeight_(height)
        config.setMinimumFrameInterval_(CM.CMTimeMake(1, settings.fps))
        config.setPixelFormat_(_PIXEL_FORMAT_420V)
        config.setShowsCursor_(True)
        config.setQueueDepth_(5)
        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        self._output: _StreamOutput = _StreamOutput.alloc().initWithCapture_(self)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, self._output)
        added, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, SCK.SCStreamOutputTypeScreen, None, None
        )
        if not added:
            VT.VTCompressionSessionInvalidate(self._session)
            raise DesktopError("ScreenCaptureKit refused the stream's output", {"error": str(error)})
        self.size: tuple[int, int] = (width, height)

    def start(self) -> None:
        with self._lifecycle:
            # Marked before it is asked: a start that times out may still start the stream, which the close then stops.
            self._started = True
            _await_answer(self._stream.startCaptureWithCompletionHandler_, "startCapture")

    def close(self) -> None:
        """Stop the stream unless macOS stopped it already, and let the encoder go whatever the stop answered."""
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
            try:
                if self._started and not self._stream_stopped.is_set():
                    _await_answer(self._stream.stopCaptureWithCompletionHandler_, "stopCapture")
            finally:
                VT.VTCompressionSessionInvalidate(self._session)

    def want_key(self) -> None:
        self._key_wanted.set()

    def encode(self, sample: object) -> None:
        """Hand a captured frame to the encoder; frames ScreenCaptureKit marks as not complete (an unchanged screen
        repeats none) are left out."""
        attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample, False)
        if attachments:
            status = attachments[0].get(SCK.SCStreamFrameInfoStatus)
            if status is not None and status != SCK.SCFrameStatusComplete:
                return
        pixels = CM.CMSampleBufferGetImageBuffer(sample)
        if pixels is None:
            return
        options: dict[object, object] | None = None
        if self._key_wanted.is_set():
            self._key_wanted.clear()
            options = {VT.kVTEncodeFrameOptionKey_ForceKeyFrame: True}
        encoded: int = int(
            VT.VTCompressionSessionEncodeFrameWithOutputHandler(
                self._session,
                pixels,
                CM.CMSampleBufferGetPresentationTimeStamp(sample),
                CM.kCMTimeInvalid,
                options,
                None,
                self._encoded,
            )[0]
        )
        if encoded != 0:
            self._fail("VideoToolbox did not take a frame", encoded)

    def _encoded(self, status: int, _flags: int, sample: object | None) -> None:
        if status != 0:
            self._fail("VideoToolbox could not encode a frame", status)
            return
        if sample is None:
            return  # a frame the encoder dropped, as a real-time encoder may
        block = CM.CMSampleBufferGetDataBuffer(sample)
        length: int = int(CM.CMBlockBufferGetDataLength(block))
        copied, data = CM.CMBlockBufferCopyDataBytes(block, 0, length, None)
        if copied != 0:
            self._fail("the encoded frame could not be read", int(copied))
            return
        attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample, False)
        key: bool = not (attachments and attachments[0].get(CM.kCMSampleAttachmentKey_NotSync))
        stamp = CM.CMSampleBufferGetPresentationTimeStamp(sample)
        micros: int = int(stamp.value * 1_000_000 // max(1, stamp.timescale))
        config: bytes = b""
        if key:
            atoms = CM.CMFormatDescriptionGetExtension(
                CM.CMSampleBufferGetFormatDescription(sample),
                CM.kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms,
            )
            config = bytes(atoms["avcC"])
        self._on_frame(self, key, avc_message(KEY if key else DELTA, micros, config, bytes(data)))

    def stream_stopped(self, reason: str) -> None:
        self._stream_stopped.set()
        self._fail(f"macOS stopped the capture: {reason}", 0)

    def _fail(self, reason: str, status: int) -> None:
        if self.failed.is_set():
            return
        self.failed.set()
        self._on_failure(self, f"{reason} (status {status})" if status != 0 else reason)


def _open(settings: LiveSettings, on_frame: FrameSink, on_failure: FailureSink) -> _Capture:
    """A capture made and started; one that does not start is closed before the error goes on."""
    capture: _Capture = _Capture(settings, on_frame, on_failure)
    try:
        capture.start()
    except BaseException:
        try:
            capture.close()
        except DesktopError as error:
            log.warning("screen_watch_close_failed", error=error.message, context=error.context)
        raise
    return capture


class _StreamOutput(NSObject):  # type: ignore[misc]  # an Objective-C class: SCStreamOutput and SCStreamDelegate
    _capture: _Capture

    def initWithCapture_(self, capture: _Capture) -> "_StreamOutput":
        made: _StreamOutput = objc.super(_StreamOutput, self).init()
        made._capture = capture
        return made

    def stream_didOutputSampleBuffer_ofType_(self, _stream: object, sample: object, kind: int) -> None:
        if kind == SCK.SCStreamOutputTypeScreen:
            self._capture.encode(sample)

    def stream_didStopWithError_(self, _stream: object, error: object) -> None:
        self._capture.stream_stopped(str(error))


def _await_answer(start: object, what: str) -> None:
    """Call a ScreenCaptureKit method that answers through a completion handler with an error or nothing, and wait."""
    done: threading.Event = threading.Event()
    errors: list[object] = []

    def completion(error: object | None) -> None:
        if error is not None:
            errors.append(error)
        done.set()

    start(completion)  # type: ignore[operator]  # a bound PyObjC method
    if not done.wait(_ANSWER_S):
        raise DesktopError(f"{what}: ScreenCaptureKit did not answer in time", {"api": what, "timeout_s": _ANSWER_S})
    if errors:
        error = errors[0]
        context: dict[str, str | int] = {"api": what, "code": int(error.code()), "description": str(error)}  # type: ignore[attr-defined]
        if int(error.code()) == _USER_DECLINED:  # type: ignore[attr-defined]
            raise DesktopPermissionError("macOS has not granted Screen Recording to the app that runs Dot", context)
        raise DesktopError(f"{what} failed", context)


def _main_display() -> object:
    done: threading.Event = threading.Event()
    answer: list[tuple[object | None, object | None]] = []

    def completion(content: object | None, error: object | None) -> None:
        answer.append((content, error))
        done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(completion)
    if not done.wait(_ANSWER_S):
        raise DesktopError("ScreenCaptureKit did not list the displays in time", {"timeout_s": _ANSWER_S})
    content, error = answer[0]
    if content is None:
        code: int = int(error.code()) if error is not None else 0  # type: ignore[attr-defined]
        if code == _USER_DECLINED:
            raise DesktopPermissionError(
                "macOS has not granted Screen Recording to the app that runs Dot", {"code": code}
            )
        raise DesktopError("ScreenCaptureKit listed no displays", {"code": code, "error": str(error)})
    main_id: int = int(Quartz.CGMainDisplayID())
    for display in content.displays():  # type: ignore[attr-defined]
        if int(display.displayID()) == main_id:
            return display
    raise DesktopError("the main display is not among those ScreenCaptureKit lists", {"display_id": main_id})


def _display_pixels(display_id: int) -> tuple[int, int]:
    mode = Quartz.CGDisplayCopyDisplayMode(display_id)
    return int(Quartz.CGDisplayModeGetPixelWidth(mode)), int(Quartz.CGDisplayModeGetPixelHeight(mode))


def _encoder(width: int, height: int, settings: LiveSettings) -> object:
    status, session = VT.VTCompressionSessionCreate(
        None, width, height, CM.kCMVideoCodecType_H264, None, None, None, None, None, None
    )
    if status != 0:
        raise DesktopError("VideoToolbox made no H.264 encoder", {"status": status, "width": width, "height": height})
    properties: dict[object, object] = {
        VT.kVTCompressionPropertyKey_RealTime: True,
        VT.kVTCompressionPropertyKey_ProfileLevel: VT.kVTProfileLevel_H264_High_AutoLevel,
        # A frame is sent as soon as it is encoded: none waits for a later one.
        VT.kVTCompressionPropertyKey_AllowFrameReordering: False,
        VT.kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration: settings.key_every_s,
        VT.kVTCompressionPropertyKey_AverageBitRate: settings.bitrate,
        VT.kVTCompressionPropertyKey_ExpectedFrameRate: settings.fps,
    }
    for key, value in properties.items():
        set_status: int = int(VT.VTSessionSetProperty(session, key, value))
        if set_status != 0:
            VT.VTCompressionSessionInvalidate(session)
            raise DesktopError("VideoToolbox refused a setting of the encoder", {"key": str(key), "status": set_status})
    prepared: int = int(VT.VTCompressionSessionPrepareToEncodeFrames(session))
    if prepared != 0:
        VT.VTCompressionSessionInvalidate(session)
        raise DesktopError("VideoToolbox could not prepare the encoder", {"status": prepared})
    return session


class LiveScreen:
    """The live stream of the main display, shared by its viewers (module docstring)."""

    def __init__(self, settings: LiveSettings) -> None:
        self._settings: LiveSettings = settings
        self._viewers: set[Viewer] = set()
        self._capture: _Capture | None = None
        self._changing: asyncio.Lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # The tasks that close captures, held so that none is collected while it runs.
        self._closing: set[asyncio.Task[None]] = set()

    def check_allowed(self) -> None:
        """Raise when macOS has not granted Screen Recording to the app that runs Dot, before a stream is answered."""
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise DesktopPermissionError("macOS has not granted Screen Recording to the app that runs Dot", {})

    async def frames(self) -> AsyncGenerator[bytes]:
        """The frames of the screen from the next key frame on, until the capture fails, :meth:`end_all` or the reader
        goes. The capture starts with its first viewer. Leaving takes no ``await``: a stream whose client went is
        cancelled again at every one, and its viewer would stay."""
        viewer: Viewer = Viewer()
        self._viewers.add(viewer)
        try:
            await self._ensure_capture()
            while True:
                message: bytes | None = await viewer.frames.get()
                if message is None:
                    return
                yield message
        finally:
            self._viewers.discard(viewer)
            if not self._viewers:
                self._spawn(self._stop_when_unwatched())

    def end_all(self) -> None:
        """End every viewer's frames (the daemon stops, or the capture failed); the capture stops once they have
        gone."""
        for viewer in self._viewers:
            while not viewer.frames.empty():
                viewer.frames.get_nowait()
            viewer.frames.put_nowait(None)

    async def _ensure_capture(self) -> None:
        async with self._changing:
            if self._capture is None:
                self._loop = asyncio.get_running_loop()
                opening: asyncio.Future[_Capture] = asyncio.ensure_future(
                    asyncio.to_thread(_open, self._settings, self._from_encoder, self._from_failure)
                )
                try:
                    capture: _Capture = await asyncio.shield(opening)
                except asyncio.CancelledError:
                    # The viewer went while the capture started: it is closed once it has.
                    opening.add_done_callback(self._close_opened)
                    raise
                if capture.failed.is_set():
                    self._close_later(capture)
                    raise DesktopError("the capture failed as it started", {"size": list(capture.size)})
                self._capture = capture
                log.info("screen_watch_started", width=capture.size[0], height=capture.size[1], fps=self._settings.fps)
            self._capture.want_key()

    async def _stop_when_unwatched(self) -> None:
        async with self._changing:
            if self._viewers or self._capture is None:
                return
            capture: _Capture = self._capture
            self._capture = None
            await asyncio.to_thread(capture.close)
            log.info("screen_watch_stopped")

    def _from_encoder(self, capture: _Capture, key: bool, message: bytes) -> None:
        loop: asyncio.AbstractEventLoop | None = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._deliver, capture, key, message)

    def _from_failure(self, capture: _Capture, reason: str) -> None:
        log.warning("screen_watch_cut", reason=reason)
        loop: asyncio.AbstractEventLoop | None = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._drop, capture)

    def _drop(self, capture: _Capture) -> None:
        """A capture that failed: no longer the one viewers read, closed, and its viewers ended."""
        if capture is not self._capture:
            return
        self._capture = None
        self._close_later(capture)
        self.end_all()

    def _close_opened(self, opening: asyncio.Future[_Capture]) -> None:
        if not opening.cancelled() and opening.exception() is None:
            self._close_later(opening.result())

    def _close_later(self, capture: _Capture) -> None:
        self._spawn(asyncio.to_thread(capture.close))

    def _spawn(self, work: Coroutine[None, None, None]) -> None:
        task: asyncio.Task[None] = asyncio.get_running_loop().create_task(work)
        self._closing.add(task)
        task.add_done_callback(self._closed)

    def _closed(self, task: asyncio.Task[None]) -> None:
        self._closing.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error: BaseException | None = task.exception()
            log.error("screen_watch_close_failed", error=str(error), kind=type(error).__name__)

    def _deliver(self, capture: _Capture, key: bool, message: bytes) -> None:
        # Frames of a capture that is no longer the one viewers read (failed, or left to close) go nowhere.
        if capture is not self._capture:
            return
        for viewer in self._viewers:
            if viewer.needs_key and not key:
                continue
            if viewer.frames.full():
                # Behind: what waits is dropped, and the viewer goes on from the next key frame.
                while not viewer.frames.empty():
                    viewer.frames.get_nowait()
                viewer.needs_key = True
                capture.want_key()
                continue
            viewer.needs_key = False
            viewer.frames.put_nowait(message)
