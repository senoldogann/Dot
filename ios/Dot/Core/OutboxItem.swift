import Foundation

/// What the owner did that the daemon has not yet heard, kept on disk until it has: a message, an answer to a card, a
/// stop, a change of the queue. Each carries what is needed to send it again as it was, and the id it was made with:
/// for a message that id is the key the daemon starts it once by (`send_once`), so that a message sent twice because an
/// answer was lost is still one message.
struct OutboxItem: Codable, Sendable, Equatable, Identifiable {
    enum Action: Codable, Sendable, Equatable {
        /// `shown` is what the bubble says, `typed` what Edit puts back in the box, `text` what the daemon is sent; `images`
        /// are file names in the outbox's folder, `uploaded` the daemon's ids of those already kept there.
        case message(thread: String, text: String, typed: String, shown: String, choices: MessageChoices, images: [String], uploaded: [String])
        case decision(approval: String, run: String, thread: String, approve: Bool, note: String)
        case stop(run: String, thread: String)
        case reorder(thread: String, ids: [String])
        case removeQueued(thread: String, message: String)
        case steerQueued(thread: String, message: String)
    }

    enum Phase: String, Codable, Sendable {
        /// Not sent yet, or sent and not heard back: it goes when the link is up, in its turn.
        case waiting
        case sending
        /// Not sent and will not be sent by itself: the daemon refused it, or it is too old to send again safely.
        case failed
    }

    let id: String
    let action: Action
    var createdAt: Double
    var attempts: Int = 0
    var phase: Phase = .waiting
    /// Words for the owner when it failed or has waited (what the daemon said, or that it is too old).
    var note: String?

    /// The chat the item belongs to.
    var thread: String {
        switch action {
        case .message(let thread, _, _, _, _, _, _), .decision(_, _, let thread, _, _), .stop(_, let thread),
            .reorder(let thread, _), .removeQueued(let thread, _), .steerQueued(let thread, _):
            thread
        }
    }

    var isMessage: Bool {
        if case .message = action { return true }
        return false
    }

    /// How long the daemon remembers a key (`send_once` keeps it a day): past this a message sent again could start twice
    /// if its first sending went through unseen, so it is the owner's to send again.
    static let keyKept: Double = 23 * 3600
}

/// How an item ended, for the screen that waits on it.
enum OutboxOutcome: Sendable, Equatable {
    /// A message the daemon took (`queue` is the chat's queue after it, and says if it started a chat of its own).
    case delivered(OutboxItem, queue: QueueView)
    /// An answer the daemon took; `status` is how the card stands now (`approved`, `denied`).
    case answered(OutboxItem, status: String)
    /// An answer that came too late: the card is no longer open (`expired`, or already `approved`/`denied` elsewhere).
    case expired(OutboxItem, status: String)
    /// A change or a stop the daemon took (or found already made).
    case done(OutboxItem)
    /// The daemon refused it for good; `words` say why.
    case refused(OutboxItem, words: String)
}

/// The outbox's file: its items in the order they were made.
struct OutboxFile: Codable, Sendable, Equatable {
    static let schema: Int = 1
    var schema: Int = OutboxFile.schema
    var items: [OutboxItem] = []
}
