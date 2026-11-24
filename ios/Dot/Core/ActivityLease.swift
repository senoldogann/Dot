import Foundation

/// What the app tells the daemon about itself (`POST /api/phone/activity`, item 1420): that it is in front of the owner and
/// which chat it shows, or that it left. While the daemon holds the lease it does not push the end of a task to this phone,
/// since the app shows it itself; a lease that is not said again ends by itself, which is the safe side: the alerts go.
struct ActivityReport: Equatable, Sendable, Encodable {
    enum State: String, Equatable, Sendable, Encodable {
        case foreground
        case background
    }

    let state: State
    /// The chat on the screen (its thread id), `nil` when the app shows none.
    let chat: String?
    /// How many seconds the daemon holds the lease unless it is told again.
    let ttl: Int

    enum CodingKeys: String, CodingKey {
        case state, chat
        case ttl = "ttl_s"
    }

    /// Writes `chat` as `null` when the app shows no chat, rather than leaving the key out: it says there is none.
    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(state, forKey: .state)
        try container.encode(chat, forKey: .chat)
        try container.encode(ttl, forKey: .ttl)
    }
}

/// The phases of the app's scene as the lease reads them.
enum LeaseScene: Equatable, Sendable {
    case active
    case inactive
    case background
}

/// When the app says it is here, with no clock and no network: every decision takes the time as a parameter, so a test
/// plays an hour in a moment (item 1440).
///
/// - the scene becoming active says `foreground` at once, and again every 25 seconds while it stays active;
/// - the scene becoming inactive (a system sheet, the app switcher) or going to the background says `background` at once,
///   once: a second `background` after it says nothing new;
/// - the chat on the screen changing says `foreground` at once with the new chat; while the app is not active it is only
///   kept, and the next `foreground` carries it;
/// - an app that is not paired sends nothing, not even `background`: it has no key to say it with.
struct ActivityLeaseRules: Equatable, Sendable {
    /// How often a lease that holds is said again, and how long the daemon holds it: the second is nearly twice the first,
    /// so that one renewal lost on a poor link does not end it.
    static let renewEvery: Double = 25
    static let ttl: Int = 45
    /// The longest chat id the daemon takes (`POST /api/phone/activity`): a longer one would be refused whole.
    static let chatLimit: Int = 64

    enum Event: Equatable, Sendable {
        case paired(Bool)
        case scene(LeaseScene)
        case chat(String?)
        case renewal
    }

    private(set) var paired: Bool
    private(set) var scene: LeaseScene
    private(set) var chat: String?
    /// A `foreground` was sent (or tried) and no `background` after it: the daemon may hold a lease.
    private(set) var held: Bool = false
    private var lastSentAt: Double?

    init(paired: Bool = false, scene: LeaseScene = .background, chat: String? = nil) {
        self.paired = paired
        self.scene = scene
        self.chat = Self.valid(chat: chat)
    }

    /// The report this event calls for at `now`, if any.
    mutating func apply(_ event: Event, now: Double) -> ActivityReport? {
        switch event {
        case .paired(let value):
            guard value != paired else { return nil }
            paired = value
            guard value else {
                held = false
                lastSentAt = nil
                return nil
            }
            return scene == .active ? foreground(now) : nil
        case .scene(let next):
            scene = next
            guard paired else { return nil }
            return next == .active ? foreground(now) : ending()
        case .chat(let next):
            let shown: String? = Self.valid(chat: next)
            guard shown != chat else { return nil }
            chat = shown
            return paired && scene == .active ? foreground(now) : nil
        case .renewal:
            return paired && scene == .active ? foreground(now) : nil
        }
    }

    /// How long until the lease is due to be said again, or `nil` when none is due (the app is not active, or not paired).
    func secondsToRenewal(now: Double) -> Double? {
        guard paired, scene == .active, let lastSentAt else { return nil }
        return max(0, lastSentAt + Self.renewEvery - now)
    }

    /// A thread id as the daemon takes it (1 to 64 characters), or `nil`: a chat id it would refuse is not sent, and the lease
    /// holds without it.
    static func valid(chat: String?) -> String? {
        guard let chat, !chat.isEmpty, chat.count <= chatLimit else { return nil }
        return chat
    }

    private mutating func foreground(_ now: Double) -> ActivityReport {
        held = true
        lastSentAt = now
        return ActivityReport(state: .foreground, chat: chat, ttl: Self.ttl)
    }

    private mutating func ending() -> ActivityReport? {
        guard held else { return nil }
        held = false
        lastSentAt = nil
        return ActivityReport(state: .background, chat: nil, ttl: Self.ttl)
    }
}

/// The lease as the app runs it: the rules above, a clock and a sender that can be replaced, and one timer. Everything
/// happens on the main actor, in the order the app says it, so a `background` never overtakes the `foreground` before it.
///
/// A report is sent by one task at a time and only the newest one waits behind it: a poor link never piles requests up,
/// and the daemon hears the last thing the app said. `send` does not throw: a report that did not get through is the
/// sender's to log, never the owner's to see (the lease ends by itself, and the alerts go, which is the safe side).
@MainActor
final class ActivityLease {
    typealias Send = @Sendable (ActivityReport) async -> Void
    /// Waits that many seconds; `false` when the wait was cancelled.
    typealias Sleep = @Sendable (Double) async -> Bool

    private var rules: ActivityLeaseRules
    private let clock: @Sendable () -> Double
    private let send: Send
    private let delay: Sleep
    private var waiting: ActivityReport?
    private var sending: Bool = false
    private var timer: Task<Void, Never>?

    init(
        paired: Bool, clock: @escaping @Sendable () -> Double = { ProcessInfo.processInfo.systemUptime },
        sleep: @escaping Sleep = { seconds in
            do {
                try await Task.sleep(for: .seconds(seconds))
                return true
            } catch {
                return false
            }
        }, send: @escaping Send
    ) {
        self.rules = ActivityLeaseRules(paired: paired)
        self.clock = clock
        self.delay = sleep
        self.send = send
    }

    /// Whether the daemon may hold a lease for this phone, as far as the app knows.
    var isHeld: Bool { rules.held }

    func scene(_ phase: LeaseScene) { take(.scene(phase)) }

    /// The chat on the screen changed (`nil`: none).
    func showing(chat: String?) { take(.chat(chat)) }

    /// The phone's key is gone or the app is leaving the daemon: nothing is said any more.
    func stop() {
        take(.paired(false))
        waiting = nil
    }

    private func take(_ event: ActivityLeaseRules.Event) {
        let now: Double = clock()
        if let report: ActivityReport = rules.apply(event, now: now) { queue(report) }
        arm(now: now)
    }

    private func queue(_ report: ActivityReport) {
        waiting = report
        guard !sending else { return }
        sending = true
        Task { await drain() }
    }

    private func drain() async {
        while let next: ActivityReport = waiting {
            waiting = nil
            await send(next)
        }
        sending = false
    }

    private func arm(now: Double) {
        timer?.cancel()
        timer = nil
        guard let wait: Double = rules.secondsToRenewal(now: now) else { return }
        let delay: Sleep = delay
        timer = Task { [weak self] in
            if await delay(wait) { self?.take(.renewal) }
        }
    }
}
