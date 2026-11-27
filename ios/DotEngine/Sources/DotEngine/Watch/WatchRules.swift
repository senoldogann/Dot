import Foundation

// The rules of watching from the phone (item 1838): what a run drives (its browser or the Mac's desktop), whether the
// owner holds it, what it is doing this moment for the picture's caption, a frame of the browser's cast, how smooth the
// picture is, and which quality of the Mac's screen to read. Pure.

// MARK: The browser's cast

/// One picture of a run's browser tab as Chrome painted it (`GET /api/runs/{id}/screencast`, event `frame`).
public struct CastFrame: Sendable, Equatable {
    public let url: String
    public let jpeg: Data

    public init(url: String, jpeg: Data) {
        self.url = url
        self.jpeg = jpeg
    }
}

private struct CastFrameWire: Decodable {
    let url: String
    let jpeg: String

    enum CodingKeys: String, CodingKey {
        case url
        case jpeg = "jpeg_b64"
    }
}

/// The frame a message of the cast carries; `nil` for a keep-alive or a message that is not a frame.
public func castFrame(of message: SSEMessage) -> CastFrame? {
    guard message.event == "frame", let wire: CastFrameWire = try? JSONDecoder().decode(CastFrameWire.self, from: Data(message.data.utf8)),
        let jpeg: Data = Data(base64Encoded: wire.jpeg)
    else { return nil }
    return CastFrame(url: wire.url, jpeg: jpeg)
}

// MARK: What a run drives

/// Where a run acts that the phone can show live: its browser (the cast of its tab) or the Mac's desktop (the screen).
public enum RunSurface: String, Sendable, Equatable {
    case browser
    case desktop
}

/// The surface of the newest step of `events` that used one; `nil` while the run has used neither.
public func runSurface(_ events: [EventRecord]) -> RunSurface? {
    for event in events.reversed() where event.kind == "tool_call" {
        guard let tool: String = event.data["tool"]?.string else { continue }
        if tool.hasPrefix("browser_") { return .browser }
        if tool.hasPrefix("desktop_") { return .desktop }
    }
    return nil
}

/// Whether the owner holds a run (takeover until handback), and whether the hold began on a screen (from the phone or
/// the page: input goes to the page) rather than on the Mac.
public enum RunHold: Sendable, Equatable {
    case free
    case held(onScreen: Bool)

    public var isHeld: Bool { self != .free }
}

/// The hold of a run by its newest `takeover` or `handback` event.
public func runHold(_ events: [EventRecord]) -> RunHold {
    guard let last: EventRecord = events.last(where: { $0.kind == "takeover" || $0.kind == "handback" }), last.kind == "takeover" else {
        return .free
    }
    return .held(onScreen: last.data["by"]?.string == "screen")
}

// MARK: What a run does now

/// What the run does to its page or the desktop this moment, for the picture's caption: the call itself, as the chat's
/// work log names it (its tool and target), so the live view and the chat say a call alike (item 1996).
public struct LiveAction: Sendable, Equatable {
    public let call: HelperStep

    public init(call: HelperStep) {
        self.call = call
    }
}

/// The newest call of `events` on a browser or the desktop that has no result yet, as an action; `nil` between steps.
public func liveAction(_ events: [EventRecord]) -> LiveAction? {
    guard !events.contains(where: { $0.kind == "run_finished" }) else { return nil }
    let answered: Set<String> = Set(events.filter { $0.kind == "tool_result" }.compactMap { $0.data["call_id"]?.string })
    for event in events.reversed() where event.kind == "tool_call" {
        guard let tool: String = event.data["tool"]?.string, tool.hasPrefix("browser_") || tool.hasPrefix("desktop_") else { continue }
        if let call: String = event.data["call_id"]?.string, answered.contains(call) { return nil }
        return helperStep(of: event).map(LiveAction.init(call:))
    }
    return nil
}

// MARK: Smoothness

/// How the picture comes: when the first frame came after the view opened, and the frames of the last two seconds.
public struct LiveFrameMeter: Sendable, Equatable {
    public private(set) var opened: Double
    public private(set) var firstFrame: Double?
    public private(set) var recent: [Double]
    public private(set) var frames: Int

    static let window: Double = 2

    public init(opened: Double) {
        self.opened = opened
        firstFrame = nil
        recent = []
        frames = 0
    }

    /// The meter with a frame seen at `now` (seconds).
    public func seeing(at now: Double) -> LiveFrameMeter {
        var next: LiveFrameMeter = self
        if next.firstFrame == nil { next.firstFrame = now }
        next.frames += 1
        next.recent = next.recent.filter { now - $0 < Self.window } + [now]
        return next
    }

    /// Milliseconds from opening to the first frame; `nil` before it.
    public var firstFrameMs: Int? { firstFrame.map { Int((($0 - opened) * 1000).rounded()) } }

    /// Frames a second over the last two seconds at `now`.
    public func fps(at now: Double) -> Double {
        let inWindow: [Double] = recent.filter { now - $0 < Self.window }
        return Double(inWindow.count) / Self.window
    }
}

// MARK: Quality of the Mac's screen

/// The quality the daemon sends the screen in (`?quality=`, item 1837).
public enum WatchQuality: String, Sendable, Equatable, CaseIterable {
    case hd
    case light
}

/// The owner's choice in the viewer: Auto (the link decides) or one quality.
public enum WatchQualityChoice: String, Sendable, Equatable, CaseIterable {
    case auto
    case hd
    case light
}

/// How far behind the Mac the picture is: each frame's arrival less its capture time, against the smallest such
/// difference seen (the clocks differ, the smallest is the link's own delay), in seconds.
public struct LagMeter: Sendable, Equatable {
    public private(set) var base: Double?
    /// The frames in a row that came more than `behind` seconds late.
    public private(set) var lateInARow: Int

    /// A picture this late is behind: the daemon drops what waits once half a second is queued on its side, so more than
    /// that is the link's queue.
    public static let behind: Double = 1.0
    /// This many late frames in a row make the link too slow for the quality.
    public static let tooSlowAfter: Int = 12

    public init() {
        base = nil
        lateInARow = 0
    }

    /// The meter with a frame captured at `micros` seen at `arrival` seconds.
    public func seeing(micros: UInt64, arrival: Double) -> LagMeter {
        var next: LagMeter = self
        let offset: Double = arrival - Double(micros) / 1_000_000
        let base: Double = min(next.base ?? offset, offset)
        next.base = base
        next.lateInARow = offset - base > Self.behind ? next.lateInARow + 1 : 0
        return next
    }

    public var tooSlow: Bool { lateInARow >= Self.tooSlowAfter }
}

/// The quality to read: the owner's choice, or for Auto the light one on a link that costs or is constrained (cellular,
/// Low Data Mode) and after the picture fell behind in HD.
public func watchQuality(choice: WatchQualityChoice, costlyLink: Bool, fellBehindInHD: Bool) -> WatchQuality {
    switch choice {
    case .hd: .hd
    case .light: .light
    case .auto: costlyLink || fellBehindInHD ? .light : .hd
    }
}
