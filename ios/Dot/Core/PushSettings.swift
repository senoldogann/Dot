import Foundation

/// Which of the three kinds of push Dot tells this phone (`GET` and `PUT /api/phone/push-settings`, item 1421). A phone that
/// never set them is told all three.
struct PushSettings: Codable, Equatable, Sendable {
    var needsYou: Bool
    var ready: Bool
    var done: Bool

    enum CodingKeys: String, CodingKey {
        case needsYou = "needs_you"
        case ready, done
    }

    static let all: PushSettings = PushSettings(needsYou: true, ready: true, done: true)

    subscript(kind: PushKind) -> Bool {
        get {
            switch kind {
            case .needsYou: needsYou
            case .ready: ready
            case .done: done
            }
        }
        set {
            switch kind {
            case .needsYou: needsYou = newValue
            case .ready: ready = newValue
            case .done: done = newValue
            }
        }
    }
}

/// The three switches, in the app's own words (what each means, in one line, as `docs/phone.md` says it).
enum PushKind: CaseIterable, Sendable, Identifiable {
    case needsYou
    case ready
    case done

    var id: Self { self }

    var title: String {
        switch self {
        case .needsYou: "Needs you"
        case .ready: "Ready"
        case .done: "Done"
        }
    }

    var words: String {
        switch self {
        case .needsYou: "Dot waits on your answer, or something it does on its own needs a look."
        case .ready: "A draft is ready to send, or Dot has news for you."
        case .done: "A task, a schedule or a campaign has finished, or failed."
        }
    }

    var symbol: String {
        switch self {
        case .needsYou: "hand.raised.fill"
        case .ready: "tray.and.arrow.down.fill"
        case .done: "checkmark.circle.fill"
        }
    }
}

/// What the Notifications switches show and do (item 1441). A flip shows at once and is sent as all three together; if the
/// daemon does not take it the switches go back to what it holds and a plain line says so. Without the daemon's answer the
/// last switches the phone knew are shown dimmed and cannot be flipped (a flip that cannot be kept is no flip). No clock,
/// no network: the screen's model sends what this says to send.
struct PushSwitches: Equatable, Sendable {
    /// What the daemon held at its last answer (a read, or what a write kept): where a refused flip goes back to.
    private(set) var confirmed: PushSettings?
    /// What the switches show.
    private(set) var shown: PushSettings
    /// Whether the daemon has answered since the screen opened: until it has, what is shown is what the phone knew, dimmed.
    private(set) var isLive: Bool = false
    /// The plain line for a flip that was not kept, until the next flip or read.
    private(set) var failure: String?
    /// The write on its way (what it carries).
    private(set) var sending: PushSettings?
    /// The owner flipped again while a write was on its way: it is followed by one that carries the latest.
    private var dirty: Bool = false

    /// `known` is what the phone kept from the last time (nothing the first time: every switch on, as the daemon says for a
    /// phone that never set them, dimmed until it is read).
    init(known: PushSettings? = nil) {
        confirmed = known
        shown = known ?? .all
    }

    /// Whether a switch may be flipped: the daemon has answered, and its answer is what the switches hold.
    var canFlip: Bool { isLive && confirmed != nil }

    /// The line over the switches while they are not live: what is shown and why it may be old.
    var offlineLine: String? {
        guard !isLive else { return nil }
        return confirmed == nil
            ? "Dot can't be reached, so these switches are not read yet."
            : "Dot can't be reached. These are the switches as the phone last knew them."
    }

    /// The daemon answered a read.
    mutating func loaded(_ settings: PushSettings) {
        confirmed = settings
        isLive = true
        failure = nil
        // A flip on its way is the owner's newest word: a read that arrives before its answer does not undo it.
        if sending == nil { shown = settings }
    }

    /// A read did not get through: the last known switches stay, dimmed.
    mutating func loadFailed() { isLive = false }

    /// The owner flipped `kind`. Returns the switches to write now, or `nil` when none is to be written yet (a write is on
    /// its way and will be followed by one with this flip, or the flip is no change, or the switches cannot be flipped).
    mutating func flip(_ kind: PushKind, to on: Bool) -> PushSettings? {
        guard canFlip, shown[kind] != on else { return nil }
        failure = nil
        shown[kind] = on
        if sending != nil {
            dirty = true
            return nil
        }
        sending = shown
        return shown
    }

    /// The write was kept. Returns the next write when the owner flipped meanwhile.
    mutating func saved(_ kept: PushSettings) -> PushSettings? {
        confirmed = kept
        isLive = true
        if dirty {
            dirty = false
            sending = shown
            return shown
        }
        sending = nil
        shown = kept
        return nil
    }

    /// The write did not get through, or the daemon refused it: the switches go back to what it holds, and the line says
    /// why in words.
    mutating func failed(_ words: String) {
        sending = nil
        dirty = false
        failure = words
        if let confirmed { shown = confirmed }
    }

    /// What to tell the owner when a write failed: the daemon's refusal in plain words, or that it could not be reached.
    static func words(forStatus status: Int?) -> String {
        switch status {
        case 401: "Dot did not accept this phone's key. Forget the key in Settings and pair again."
        case 404: "Dot does not know this phone any more. Forget the key in Settings and pair again."
        case nil: "Dot could not be reached, so the switches are back as they were."
        default: "Dot could not keep that, so the switches are back as they were."
        }
    }
}
