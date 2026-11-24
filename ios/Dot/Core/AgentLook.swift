import Foundation

/// Who an avatar is, and how it is told to someone who cannot see it (item 1462). Dot wears its own face; every other agent
/// wears the bot, one neutral disc whatever its id: no hue taken from the id, no initial, so that no agent is told apart from
/// another by a colour or a letter it did not choose (the owner: "the other bots must have a bot icon too, not a letter in
/// different colours"). How it stands is the ring round it (`StateRing`), which turns while it works and is amber while it waits
/// for the owner.
enum AgentLook {
    /// Dot's id in `GET /api/agents`; a chat with no `actor_id` is Dot's.
    static let dotId: String = "dot"

    enum Face: Equatable, Sendable {
        case dot, bot
    }

    /// The face of the agent `id`: Dot's for Dot (or for none: its chat), the bot's for every other.
    static func face(ofAgent id: String?) -> Face {
        id == nil || id == dotId ? .dot : .bot
    }

    /// What VoiceOver says of an agent: its name and how it stands, "Dot, working" or "Research, waits for you"; one that is
    /// paused says so, and one at rest is its name alone.
    static func words(name: String, state: ThreadState, paused: Bool = false) -> String {
        switch state {
        case .waiting: "\(name), waits for you"
        case .working: "\(name), working"
        case .idle: paused ? "\(name), paused" : name
        }
    }
}

extension AgentSummary {
    /// How the agent stands, from its newest run: blocked is waiting for the owner, a run in hand is working.
    var state: ThreadState {
        guard let status: String = latest?.status else { return .idle }
        return status == "blocked" ? .waiting : status.isActiveRunStatus ? .working : .idle
    }

    var isPaused: Bool { record.pausedAt != nil }

    /// What VoiceOver says of it (`AgentLook.words`).
    var spokenWords: String { AgentLook.words(name: record.name, state: state, paused: isPaused) }
}

/// Which of an agent's lines in a chat wears its face (item 1462): the first since someone else spoke, as the web app's line
/// "starts with the face of who works" (item 859). The lines that follow line up with its words. What stands between two of
/// its lines without anyone else speaking (its steps, a card it asks, a note) is not someone speaking.
enum SpeakerRule {
    static func wearsFace(at index: Int, in items: [ChatItem]) -> Bool {
        guard items.indices.contains(index), items[index].kind.isAgentLine else { return false }
        for earlier in items[..<index].reversed() {
            switch earlier.kind {
            case .user: return true
            case .assistant, .answer: return false
            case .tool, .approval, .note, .shownPage: continue
            }
        }
        return true
    }
}

extension ChatItem.Kind {
    /// The agent speaks: a message it wrote along the way, or its answer.
    var isAgentLine: Bool {
        switch self {
        case .assistant, .answer: true
        case .user, .tool, .approval, .note, .shownPage: false
        }
    }
}
