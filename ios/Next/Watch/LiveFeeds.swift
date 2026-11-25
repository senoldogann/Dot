import AVFoundation
import DotEngine
import Foundation
import Network
import Observation
import UIKit
import os

// The two live pictures (items 1838-1839): the Mac's screen (H.264, `ScreenFeed`) and a run's browser tab (JPEG frames,
// `CastFeed`). Each reads on a task of its own, off the main thread; the main thread hears the picture's size, the first
// frame and the frame rate at most twice a second, so the chat under a mini player never stutters. Nothing is read while
// no view shows the picture: the views start and stop the feeds, and stop them when the app leaves the screen.

/// What stands in place of the picture, or that it is live.
enum FeedState: Equatable, Sendable {
    case idle
    case connecting
    case live
    /// The link broke or the Mac's capture failed; it is tried again by itself.
    case reconnecting(reason: String)
    /// The daemon refused (no Screen Recording, a run that is not active): tried again only on Retry.
    case stopped(reason: String)
    /// The run ended: its browser has no page left to cast.
    case ended
}

/// What the reading task tells the main thread.
private enum FeedReport: Sendable {
    case size(CGSize)
    case meter(firstFrameMs: Int?, fps: Double)
    case behind
}

/// How long the feed waits before the next try: 1, 2, 4, then 8 s.
func feedRetryDelay(attempt: Int) -> Duration { .seconds(min(8, 1 << min(max(attempt - 1, 0), 3))) }

/// The words of a failed read: the daemon's own, else what went wrong on the way.
func feedReason(_ error: any Error) -> String {
    if let failure = error as? DaemonRequestError {
        if !failure.detail.isEmpty { return failure.detail }
        // The daemon's 5xx with no words of its own: on the screen, its capture did not start (a busy or sleeping Mac).
        if let code = failure.statusCode, code >= 500 {
            return String(localized: "The Mac's screen capture did not start; trying again.", comment: "Live picture: the daemon failed to start the capture (5xx)")
        }
        if case .transport(_, let underlying) = failure { return underlying.localizedDescription }
        return failure.description
    }
    if let failure = error as? VideoFeedError { return failure.description }
    return error.localizedDescription
}

/// Whether a failed read is worth trying again by itself: the link, the daemon's 5xx (the Mac's capture failed as it
/// started), a stream that broke; not a refusal (4xx).
func feedRetries(_ error: any Error) -> Bool {
    guard let failure = error as? DaemonRequestError else { return true }
    return failure.isRetryable
}

/// Debug builds: the meter's numbers, for the PR's measurements (`Library/Caches/dotnext-watch.txt`).
enum WatchProbe {
    static func note(_ line: String) {
        #if DEBUG
            guard let folder: URL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return }
            let file: URL = folder.appending(path: "dotnext-watch.txt")
            let stamped: Data = Data("\(Date.now.timeIntervalSince1970) \(line)\n".utf8)
            if let handle = try? FileHandle(forWritingTo: file) {
                handle.seekToEndOfFile()
                handle.write(stamped)
                try? handle.close()
            } else {
                try? stamped.write(to: file)
            }
        #endif
    }
}

// MARK: The Mac's screen

/// The Mac's screen, live (`GET /api/screen/live`), drawn on `picture` by the phone's own H.264 decoder.
@MainActor
@Observable
final class ScreenFeed {
    private(set) var state: FeedState = .idle
    private(set) var pictureSize: CGSize = CGSize(width: 1920, height: 1248)
    private(set) var fps: Double = 0
    private(set) var firstFrameMs: Int?
    /// The quality read now.
    private(set) var quality: WatchQuality = .hd
    /// The owner's choice (Settings of the viewer); Auto lets the link decide.
    var choice: WatchQualityChoice = .auto {
        didSet { if choice != oldValue, api != nil { restart() } }
    }
    @ObservationIgnored let picture: VideoPicture = VideoPicture()
    @ObservationIgnored private var api: DaemonAPI?
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var fellBehind: Bool = false
    @ObservationIgnored private var costly: Bool = false
    @ObservationIgnored private let path: NWPathMonitor = NWPathMonitor()
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "watch")

    init() {
        path.pathUpdateHandler = { [weak self] update in
            let costly: Bool = update.isExpensive || update.isConstrained
            Task { @MainActor [weak self] in self?.linkChanged(costly: costly) }
        }
        path.start(queue: DispatchQueue(label: "dev.dotagent.phone.next.watch.path"))
    }

    isolated deinit {
        path.cancel()
        task?.cancel()
    }

    var running: Bool { task != nil }

    /// Starts reading (or again, after a stop or a refusal).
    func start(api: DaemonAPI) {
        self.api = api
        restart()
    }

    /// Stops reading: the Mac stops its capture a moment after its last viewer goes.
    func stop() {
        task?.cancel()
        task = nil
        state = .idle
        fps = 0
    }

    private func linkChanged(costly: Bool) {
        guard costly != self.costly else { return }
        self.costly = costly
        if api != nil, choice == .auto, running { restart() }
    }

    private func restart() {
        guard let api else { return }
        task?.cancel()
        quality = watchQuality(choice: choice, costlyLink: costly, fellBehindInHD: fellBehind)
        firstFrameMs = nil
        fps = 0
        state = .connecting
        let quality: WatchQuality = quality
        let feeder: H264Feeder = H264Feeder(renderer: picture.displayLayer.sampleBufferRenderer)
        let report: @Sendable (FeedReport) -> Void = { [weak self] report in
            Task { @MainActor in self?.heard(report, quality: quality) }
        }
        task = Task { [weak self] in
            var attempt: Int = 0
            while !Task.isCancelled {
                let opened: Double = ProcessInfo.processInfo.systemUptime
                do {
                    try await Self.pump(api: api, quality: quality, feeder: feeder, opened: opened, report: report)
                    if Task.isCancelled { return }
                    // The Mac stopped sending (its capture failed, or it slept): tried again.
                    attempt += 1
                    self?.state = .reconnecting(reason: String(localized: "The Mac stopped sending its screen.", comment: "Watch: the live screen's stream ended (web watch.ended)"))
                } catch is CancellationError {
                    return
                } catch {
                    if Task.isCancelled { return }
                    guard feedRetries(error) else {
                        self?.state = .stopped(reason: feedReason(error))
                        self?.task = nil
                        return
                    }
                    attempt += 1
                    self?.state = .reconnecting(reason: feedReason(error))
                }
                self?.logger.warning("screen feed broke attempt=\(attempt, privacy: .public)")
                do {
                    try await Task.sleep(for: feedRetryDelay(attempt: attempt))
                } catch {
                    return
                }
                // Reconnecting, with why, stays until a frame comes.
            }
        }
    }

    private func heard(_ report: FeedReport, quality: WatchQuality) {
        guard quality == self.quality, task != nil else { return }
        switch report {
        case .size(let size):
            pictureSize = size
        case .meter(let first, let rate):
            if state != .live { state = .live }
            if firstFrameMs == nil, let first {
                firstFrameMs = first
                WatchProbe.note("screen quality=\(quality.rawValue) first_frame_ms=\(first)")
            }
            fps = rate
            WatchProbe.note("screen quality=\(quality.rawValue) fps=\(String(format: "%.1f", rate))")
        case .behind:
            // HD fell behind the link: Auto goes on in the light quality.
            guard quality == .hd, choice == .auto, !fellBehind else { return }
            fellBehind = true
            WatchProbe.note("screen fell_behind quality=hd")
            restart()
        }
    }

    /// Reads the stream until it ends, drawing each frame; tells the main thread what it needs, at most twice a second.
    private nonisolated static func pump(
        api: DaemonAPI, quality: WatchQuality, feeder: H264Feeder, opened: Double, report: @escaping @Sendable (FeedReport) -> Void
    ) async throws {
        var rest: Data = Data()
        var meter: FrameMeter = FrameMeter(opened: opened)
        var lag: LagMeter = LagMeter()
        var told: Double = 0
        var toldBehind: Bool = false
        for try await chunk in api.screenLive(quality: quality) {
            let split = try splitLiveFrames(rest.isEmpty ? chunk : rest + chunk)
            rest = split.rest
            for frame in split.frames {
                if let size: CGSize = try feeder.feed(frame) { report(.size(size)) }
                let now: Double = ProcessInfo.processInfo.systemUptime
                meter = meter.seeing(at: now)
                lag = lag.seeing(micros: frame.micros, arrival: now)
                if lag.tooSlow, !toldBehind {
                    toldBehind = true
                    report(.behind)
                }
                if meter.frames == 1 || now - told >= 0.5 {
                    told = now
                    report(.meter(firstFrameMs: meter.firstFrameMs, fps: meter.fps(at: now)))
                }
            }
        }
    }
}

// MARK: A run's browser

/// A run's browser tab as Chrome paints it (`GET /api/runs/{id}/screencast`), the newest frame on `picture`.
@MainActor
@Observable
final class CastFeed {
    let run: String
    private(set) var state: FeedState = .idle
    private(set) var pictureSize: CGSize = CGSize(width: 1280, height: 800)
    private(set) var address: String = ""
    private(set) var fps: Double = 0
    private(set) var firstFrameMs: Int?
    @ObservationIgnored let picture: UIImageView = {
        let view: UIImageView = UIImageView()
        view.contentMode = .scaleAspectFit
        view.backgroundColor = .black
        return view
    }()
    @ObservationIgnored private var task: Task<Void, Never>?

    init(run: String) {
        self.run = run
    }

    isolated deinit { task?.cancel() }

    var running: Bool { task != nil }

    func start(api: DaemonAPI) {
        task?.cancel()
        firstFrameMs = nil
        state = .connecting
        let run: String = run
        let show: @Sendable (Shown) -> Void = { [weak self] frame in
            Task { @MainActor in self?.show(frame) }
        }
        task = Task { [weak self] in
            var attempt: Int = 0
            while !Task.isCancelled {
                let opened: Double = ProcessInfo.processInfo.systemUptime
                do {
                    try await Self.pump(api: api, run: run, opened: opened, show: show)
                    if Task.isCancelled { return }
                    // The cast ends with the run.
                    self?.state = .ended
                    self?.task = nil
                    return
                } catch is CancellationError {
                    return
                } catch {
                    if Task.isCancelled { return }
                    guard feedRetries(error) else {
                        self?.state = .stopped(reason: feedReason(error))
                        self?.task = nil
                        return
                    }
                    attempt += 1
                    self?.state = .reconnecting(reason: feedReason(error))
                }
                do {
                    try await Task.sleep(for: feedRetryDelay(attempt: attempt))
                } catch {
                    return
                }
                // Reconnecting, with why, stays until a frame comes.
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        if state != .ended { state = .idle }
        fps = 0
    }

    /// A decoded frame, with the meter's numbers.
    struct Shown: @unchecked Sendable {
        let image: UIImage
        let address: String
        let firstFrameMs: Int?
        let fps: Double
    }

    private func show(_ frame: Shown) {
        guard task != nil else { return }
        picture.image = frame.image
        if frame.image.size != pictureSize { pictureSize = frame.image.size }
        if address != frame.address { address = frame.address }
        if state != .live { state = .live }
        if firstFrameMs == nil, let first = frame.firstFrameMs {
            firstFrameMs = first
            WatchProbe.note("cast first_frame_ms=\(first)")
        }
        if abs(fps - frame.fps) >= 0.5 { fps = frame.fps }
    }

    /// Reads the cast until it ends; decodes only the newest frame of what came at once, so a slow phone shows the page
    /// as it is now.
    private nonisolated static func pump(api: DaemonAPI, run: String, opened: Double, show: @escaping @Sendable (Shown) -> Void) async throws {
        var parser: SSEParser = SSEParser()
        var meter: FrameMeter = FrameMeter(opened: opened)
        var noted: Double = 0
        for try await chunk in api.screencast(run: run) {
            guard let frame: CastFrame = parser.feed(chunk).compactMap(castFrame(of:)).last else { continue }
            guard let image: UIImage = await UIImage(data: frame.jpeg)?.byPreparingForDisplay() else { continue }
            let now: Double = ProcessInfo.processInfo.systemUptime
            meter = meter.seeing(at: now)
            let fps: Double = meter.fps(at: now)
            if now - noted >= 2 {
                noted = now
                WatchProbe.note("cast fps=\(String(format: "%.1f", fps))")
            }
            show(Shown(image: image, address: frame.url, firstFrameMs: meter.firstFrameMs, fps: fps))
        }
    }
}
