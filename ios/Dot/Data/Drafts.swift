import Foundation
import os

/// The words typed in a chat's box and not sent, kept per chat: a chat left half-written has them back at the next
/// launch. A value type with the rules (an empty draft is no draft, the oldest go past a limit, no draft is a wall of
/// text); `DraftStore` keeps it on disk.
struct Drafts: Codable, Equatable, Sendable {
    struct Entry: Codable, Equatable, Sendable {
        var text: String
        var at: Double
    }

    /// How many chats keep a draft, and how long one may be (a draft longer than that is cut, as the box would not hold it).
    static let kept: Int = 60
    static let longest: Int = 20_000

    /// The key of the box of a chat that does not exist yet: the home's, or an agent's own.
    static func newChatKey(agent: String?) -> String { agent.map { "new:\($0)" } ?? "new" }

    private(set) var entries: [String: Entry] = [:]
    var schema: Int = 1

    func text(for key: String) -> String { entries[key]?.text ?? "" }

    /// Keeps `text` for the chat, or forgets the chat's draft when it is only whitespace.
    mutating func set(_ text: String, for key: String, at now: Double = Date().timeIntervalSince1970) {
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            entries[key] = nil
            return
        }
        entries[key] = Entry(text: String(text.prefix(Self.longest)), at: now)
        if entries.count > Self.kept {
            let drop: [String] = entries.sorted { $0.value.at < $1.value.at }.prefix(entries.count - Self.kept).map(\.key)
            for key in drop { entries[key] = nil }
        }
    }

    mutating func remove(_ key: String) { entries[key] = nil }
}

/// The drafts of one daemon host on disk (`drafts.json` beside the chat cache): read once, written a moment after the
/// last change and at once when asked, atomically.
@MainActor
final class DraftStore {
    private var drafts: Drafts
    private let file: URL
    private var pending: Task<Void, Never>?
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "drafts")

    init(directory: URL) {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        file = directory.appending(path: "drafts.json")
        if let data: Data = try? Data(contentsOf: file), let read: Drafts = try? JSONDecoder().decode(Drafts.self, from: data), read.schema == 1 {
            drafts = read
        } else {
            drafts = Drafts()
        }
    }

    func text(for key: String) -> String { drafts.text(for: key) }

    func save(_ text: String, for key: String) {
        guard drafts.text(for: key) != String(text.prefix(Drafts.longest)) else { return }
        drafts.set(text, for: key)
        scheduleWrite()
    }

    func remove(_ key: String) {
        drafts.remove(key)
        scheduleWrite()
    }

    /// Forgets every draft (the phone's key was forgotten).
    func clear() {
        drafts = Drafts()
        pending?.cancel()
        try? FileManager.default.removeItem(at: file)
    }

    func flush() {
        pending?.cancel()
        pending = nil
        write()
    }

    private func scheduleWrite() {
        pending?.cancel()
        pending = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(600))
            if Task.isCancelled { return }
            self?.write()
        }
    }

    private func write() {
        do {
            try JSONEncoder().encode(drafts).write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch {
            logger.error("drafts were not written: \(String(describing: error), privacy: .public)")
        }
    }
}
