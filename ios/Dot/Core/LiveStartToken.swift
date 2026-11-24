import Foundation

/// When the push-to-start token of the phone's Live Activity goes to the daemon (items 1425 and 1445): iOS gives it with
/// `Activity<DotFleetAttributes>.pushToStartTokenUpdates` and again whenever it changes, and the daemon wants each one once.
/// No clock, no network: the registrar asks, and says what it did.
struct StartTokenGate: Equatable, Sendable {
    /// The token the daemon has, as far as the app knows: the last one that was sent and taken.
    private(set) var held: String?

    /// The daemon takes the token in lowercase hex of 64 to 512 characters and refuses any other shape whole (`422`): one that
    /// is not is not sent.
    static func isValid(_ token: String) -> Bool {
        (64...512).contains(token.count) && token.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x61 && $0 <= 0x66) }
    }

    /// Whether `token` is to be sent now: it is of the shape the daemon takes and is not the one it already has.
    func shouldSend(_ token: String) -> Bool { Self.isValid(token) && token != held }

    /// The daemon took `token`.
    mutating func didSend(_ token: String) { held = token }

    /// The daemon no longer has a token (the key was forgotten, Live Activities were switched off): the next one is sent again.
    mutating func forget() { held = nil }
}
