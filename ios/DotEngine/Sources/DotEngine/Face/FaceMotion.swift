import Foundation

/// Where a face stands at one moment (the living face of a chat at work): how far it rose (`bob`, a share of its size,
/// negative up), where its eyes look (`glance`, a share of its size, negative left), how open they are (`open`, 1 open,
/// near 0 shut in a blink) and how far it has turned about its upright axis (`turn`, degrees, 0 to 360 in a flip).
public struct FacePose: Sendable, Equatable {
    public let bob: Double
    public let glance: Double
    public let open: Double
    public let turn: Double

    public init(bob: Double, glance: Double, open: Double, turn: Double) {
        self.bob = bob
        self.glance = glance
        self.open = open
        self.turn = turn
    }

    /// A face at rest: still, eyes ahead and open.
    public static let rest: FacePose = FacePose(bob: 0, glance: 0, open: 1, turn: 0)
}

/// The rhythm of the living face: calm, never in step with a face beside it.
public enum FaceRhythm {
    /// One bob, up and back, in seconds; how high it rises, as a share of the face's size.
    public static let bobPeriod: Double = 1.8
    public static let bobHeight: Double = 0.06
    /// A glance to one side and back, every so many seconds (the side changes from one glance to the next); how far
    /// the eyes go, as a share of the face's size, and how long the whole glance takes.
    public static let glanceEvery: Double = 4.2
    public static let glanceReach: Double = 0.07
    public static let glanceLength: Double = 1.5
    /// A blink every so many seconds, and how long it takes.
    public static let blinkEvery: Double = 5.3
    public static let blinkLength: Double = 0.16
    /// A flip, a whole turn with a small hop, rarely, and how long it takes; the hop's height as a share of the size.
    public static let flipEvery: Double = 16
    public static let flipLength: Double = 0.8
    public static let hopHeight: Double = 0.12
    /// Under Reduce Motion the face does not move: it breathes in brightness, from `pulseLow` to full, once a period.
    public static let pulsePeriod: Double = 2.4
    public static let pulseLow: Double = 0.55
}

/// The pose of a living face at `time` (seconds on any steady clock), its rhythm offset by `seed` (a bot's id hash) so
/// that two faces at work never move as one (pure).
public func livingPose(at time: Double, seed: UInt32) -> FacePose {
    let offset: Double = Double(seed % 997) / 997 * FaceRhythm.flipEvery
    let t: Double = time + offset
    let bobPhase: Double = positive(t, FaceRhythm.bobPeriod) / FaceRhythm.bobPeriod
    var bob: Double = -FaceRhythm.bobHeight * (0.5 - 0.5 * cos(2 * .pi * bobPhase))
    let flipAt: Double = positive(t, FaceRhythm.flipEvery)
    var turn: Double = 0
    if flipAt < FaceRhythm.flipLength {
        let progress: Double = flipAt / FaceRhythm.flipLength
        turn = 360 * easeInOut(progress)
        bob -= FaceRhythm.hopHeight * sin(.pi * progress)
    }
    let glanceRound: Double = (t / FaceRhythm.glanceEvery).rounded(.down)
    let glanceAt: Double = positive(t, FaceRhythm.glanceEvery)
    let side: Double = (Int(glanceRound) + Int(seed & 1)) % 2 == 0 ? -1 : 1
    let glance: Double = side * FaceRhythm.glanceReach * glanceShape(glanceAt / FaceRhythm.glanceLength)
    let blinkAt: Double = positive(t, FaceRhythm.blinkEvery)
    let open: Double = blinkAt < FaceRhythm.blinkLength ? 0.1 + 0.9 * abs(cos(.pi * blinkAt / FaceRhythm.blinkLength)) : 1
    return FacePose(bob: bob, glance: glance, open: open, turn: turn)
}

/// The brightness of a face at work under Reduce Motion at `time`: from `pulseLow` to 1 and back once a period (pure).
public func stillPulse(at time: Double) -> Double {
    let phase: Double = positive(time, FaceRhythm.pulsePeriod) / FaceRhythm.pulsePeriod
    return FaceRhythm.pulseLow + (1 - FaceRhythm.pulseLow) * (0.5 + 0.5 * cos(2 * .pi * phase))
}

/// `value` modulo `period`, never negative.
private func positive(_ value: Double, _ period: Double) -> Double {
    let rest: Double = value.truncatingRemainder(dividingBy: period)
    return rest < 0 ? rest + period : rest
}

private func easeInOut(_ progress: Double) -> Double {
    0.5 - 0.5 * cos(.pi * min(max(progress, 0), 1))
}

/// How far a glance has gone at `progress` of its length: out in the first fifth, held, back in the last fifth; none
/// after its length.
private func glanceShape(_ progress: Double) -> Double {
    if progress >= 1 { return 0 }
    if progress < 0.2 { return easeInOut(progress / 0.2) }
    if progress > 0.8 { return easeInOut((1 - progress) / 0.2) }
    return 1
}
