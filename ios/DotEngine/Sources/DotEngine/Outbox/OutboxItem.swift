import Foundation

/// The kind a keyed message is sent as (`sending.py`: `run`, `steer`, `queue`). The daemon keeps each kind's keys
/// apart, so a message keeps the kind of its first attempt for good (plan §11, REVIEW-architecture S5): a steer whose
/// answer was lost and whose run then ended is never sent again as a queued message under the same key.
public enum SendingKind: String, Codable, Sendable, Equatable {
    case run
    case steer
    case queue
}

/// What the owner did that the daemon has not yet heard, kept on disk until it has. Moved from the old app
/// (`ios/Dot/Core/OutboxItem.swift`) with the steer, the new chat and the chat's own changes added.
public struct OutboxItem: Codable, Sendable, Equatable, Identifiable {
    public enum Action: Codable, Sendable, Equatable {
        /// A message for a chat (`POST /threads/{id}/queue`): starts a run when none works, queued behind one that does.
        /// `images` are file names in the outbox's folder, `uploaded` the daemon's ids of those already kept there.
        case message(thread: String, text: String, images: [String], uploaded: [String], picks: MessagePicks)
        /// A message into the run at work (`POST /runs/{id}/steer`).
        case steer(run: String, thread: String, text: String, images: [String], uploaded: [String])
        /// A message that starts a chat of its own (`POST /runs`), with Dot (`agent` `nil`) or with one of the owner's
        /// agents (item 1881: a new chat from a bot's menu or profile).
        case newChat(text: String, images: [String], uploaded: [String], agent: String?)
        /// An answer to an ask; `remember` is "Always allow in this chat" (item 866), offered only where the daemon keeps it.
        case decision(approval: String, run: String, thread: String, approve: Bool, note: String, remember: Bool)
        case stop(run: String, thread: String)
        /// Stops a helper by the path its view names.
        case stopHelper(path: String, run: String, thread: String)
        case reorder(thread: String, ids: [String])
        case removeQueued(thread: String, message: String)
        case steerQueued(thread: String, message: String)
        /// A queued message changed (§20: edited in the composer); `attachments` `nil` keeps its files.
        case editQueued(thread: String, message: String, text: String, attachments: [String]?)
        case resumeQueue(thread: String)
        case rename(thread: String, title: String)
        case pin(thread: String, pinned: Bool)
        case delete(thread: String)
        /// A reaction put on (`on`) or taken off a message of Dot's (`message` is `answer` or `step-N`).
        case react(run: String, thread: String, message: String, reaction: String, on: Bool)
        /// The model and effort the chat's next follow-up starts with (`PATCH /threads/{id}` choices).
        case choices(thread: String, choices: ChatChoices)
    }

    public enum Phase: String, Codable, Sendable {
        /// Not sent yet, or sent and not heard back: it goes when the link is up, in its turn.
        case waiting
        case sending
        /// Not sent and will not be sent by itself: the daemon refused it, or it is too old to send again safely.
        case failed
    }

    /// Why an item failed, for the app to say in the owner's language.
    public enum Failure: Codable, Sendable, Equatable {
        /// The daemon refused it: its status and its own words.
        case refused(code: Int, detail: String)
        /// The run a steer was for is no longer at work; the owner may send the words as a new message.
        case runEnded(detail: String)
        /// It waited longer than the daemon keeps a key: sending it again could start it twice.
        case tooOld
        /// The answer could not be read.
        case unreadable
        /// A photo of the message could not be read from the phone; the message is kept for the owner.
        case photoMissing(name: String)
    }

    /// The item's id; for a keyed message, the key the daemon starts it once by.
    public let id: String
    public let action: Action
    /// Fixed when the item is made, never changed (plan §11).
    public let kind: SendingKind?
    public var createdAt: Double
    public var attempts: Int
    public var phase: Phase
    public var failure: Failure?

    public init(id: String, action: Action, createdAt: Double) {
        self.id = id
        self.action = action
        self.kind = Self.kind(of: action)
        self.createdAt = createdAt
        self.attempts = 0
        self.phase = .waiting
        self.failure = nil
    }

    /// The kind an action is sent as; `nil` for the doings that say the same twice (no key).
    public static func kind(of action: Action) -> SendingKind? {
        switch action {
        case .message: .queue
        case .steer: .steer
        case .newChat: .run
        default: nil
        }
    }

    /// The chat the item belongs to; `nil` for a new chat.
    public var thread: String? {
        switch action {
        case .message(let thread, _, _, _, _), .steer(_, let thread, _, _, _), .decision(_, _, let thread, _, _, _), .stop(_, let thread),
            .stopHelper(_, _, let thread), .reorder(let thread, _), .removeQueued(let thread, _), .steerQueued(let thread, _),
            .editQueued(let thread, _, _, _), .resumeQueue(let thread), .rename(let thread, _), .pin(let thread, _), .delete(let thread),
            .react(_, let thread, _, _, _), .choices(let thread, _):
            thread
        case .newChat: nil
        }
    }

    /// The words of a message, steer or new chat.
    public var text: String? {
        switch action {
        case .message(_, let text, _, _, _), .steer(_, _, let text, _, _), .newChat(let text, _, _, _): text
        default: nil
        }
    }

    public var images: [String] {
        switch action {
        case .message(_, _, let images, _, _), .steer(_, _, _, let images, _), .newChat(_, let images, _, _): images
        default: []
        }
    }

    public var isMessage: Bool { kind != nil }

    /// How long the daemon remembers a key (`send_once` keeps it a day): past this a message sent again could start twice
    /// if its first sending went through unseen, so it is the owner's to send again.
    public static let keyKept: Double = 23 * 3600

    /// The same item with the daemon's ids of its photos uploaded so far.
    func with(uploaded: [String]) -> OutboxItem {
        let next: Action =
            switch action {
            case .message(let thread, let text, let images, _, let picks): .message(thread: thread, text: text, images: images, uploaded: uploaded, picks: picks)
            case .steer(let run, let thread, let text, let images, _):
                .steer(run: run, thread: thread, text: text, images: images, uploaded: uploaded)
            case .newChat(let text, let images, _, let agent): .newChat(text: text, images: images, uploaded: uploaded, agent: agent)
            default: action
            }
        var copy: OutboxItem = OutboxItem(id: id, action: next, createdAt: createdAt)
        copy.attempts = attempts
        copy.phase = phase
        copy.failure = failure
        return copy
    }
}

/// How an item ended, for the store that draws it.
public enum OutboxOutcome: Sendable, Equatable {
    /// A message the daemon took (`queue` is the chat's queue after it).
    case delivered(OutboxItem, queue: QueueView)
    /// A steer the daemon handed to the run.
    case steered(OutboxItem)
    /// A new chat the daemon started.
    case started(OutboxItem, run: RunRecord)
    /// An answer the daemon took; `status` is how the card stands now.
    case answered(OutboxItem, status: String)
    /// An answer that came too late: the card is no longer open (answered elsewhere, expired). An outcome, not a failure.
    case expired(OutboxItem, status: String)
    /// A change or a stop the daemon took (or found already made).
    case done(OutboxItem)
    /// The daemon refused it for good.
    case refused(OutboxItem, failure: OutboxItem.Failure)
}

/// The outbox's file: its items in the order they were made.
public struct OutboxFile: Codable, Sendable, Equatable {
    public static let schema: Int = 2
    public var schema: Int
    public var items: [OutboxItem]

    public init(items: [OutboxItem]) {
        self.schema = Self.schema
        self.items = items
    }
}

/// A key for a message (`^[A-Za-z0-9-]{16,64}$`): sent again after a lost answer, the daemon starts the message once.
public func newMessageKey() -> String { UUID().uuidString }
