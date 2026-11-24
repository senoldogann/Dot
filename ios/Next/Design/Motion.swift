import SwiftUI

/// Motion tokens (plan §3): adjust 120 ms, enter 200, exit 150, a message's fade 220, a disclosure 180.
enum MotionToken: Sendable {
    case adjust
    case enter
    case exit
    case message
    case disclosure

    var seconds: Double {
        switch self {
        case .adjust: 0.12
        case .enter: 0.2
        case .exit: 0.15
        case .message: 0.22
        case .disclosure: 0.18
        }
    }
}

/// The animation of a token, or none under Reduce Motion: the change still happens, it just does not move.
func motionAnimation(_ token: MotionToken, reduceMotion: Bool) -> Animation? {
    reduceMotion ? nil : .easeOut(duration: token.seconds)
}

/// The kinds of touch feedback (plan §3): selection for a disclosure, light for copy and list actions, medium for a
/// full swipe, success and error for an outcome the owner waited for.
enum Haptic: Sendable {
    case selection
    case light
    case medium
    case success
    case error

    var feedback: SensoryFeedback {
        switch self {
        case .selection: .selection
        case .light: .impact(weight: .light)
        case .medium: .impact(weight: .medium)
        case .success: .success
        case .error: .error
        }
    }
}

extension View {
    /// Feedback each time `trigger` changes.
    func haptic<T: Equatable>(_ kind: Haptic, trigger: T) -> some View {
        sensoryFeedback(kind.feedback, trigger: trigger)
    }

    /// The animation of a token unless Reduce Motion is on.
    func motion<V: Equatable>(_ token: MotionToken, value: V, reduceMotion: Bool) -> some View {
        animation(motionAnimation(token, reduceMotion: reduceMotion), value: value)
    }
}

/// When a tick of streaming text may be felt (review S8): only for output on screen that the reader follows, never for
/// a replay or a cached insertion, at most once every 320 ms, and only when the owner turned ticks on.
struct StreamTickGate: Equatable, Sendable {
    static let interval: TimeInterval = 0.32
    let lastTick: Date?

    struct Moment: Equatable, Sendable {
        let now: Date
        let ticksEnabled: Bool
        let visible: Bool
        let following: Bool
        let isReplay: Bool
    }

    /// Whether this moment ticks, and the gate after it.
    func tick(_ moment: Moment) -> (fires: Bool, next: StreamTickGate) {
        guard moment.ticksEnabled, moment.visible, moment.following, !moment.isReplay else { return (false, self) }
        if let lastTick, moment.now.timeIntervalSince(lastTick) < Self.interval { return (false, self) }
        return (true, StreamTickGate(lastTick: moment.now))
    }
}
