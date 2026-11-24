import Foundation

/// How the ring round an avatar moves (item 1460). The ring of a working agent was a sweep that turned once in 1.4 s, which
/// the owner found "far too fast" on the phone: it now turns once in `secondsPerTurn`, evenly (a linear animation, no
/// ease at the turn), with the soft tail of its angular gradient behind the bright head. The one number is here, so that
/// it is one line to change again.
enum RingMotion {
    /// How long one full turn of a working ring takes, in seconds.
    static let secondsPerTurn: Double = 4

    /// Whether the ring of an avatar that stands as `state` turns: only while it works, and never when the reader asked for
    /// less motion (Reduce Motion), which leaves the ring drawn and still. A ring that left with the state and comes back
    /// asks again, so it starts from the top every time a chat goes back to work (item 1394).
    static func turns(state: ThreadState, reduceMotion: Bool) -> Bool {
        state == .working && !reduceMotion
    }
}
