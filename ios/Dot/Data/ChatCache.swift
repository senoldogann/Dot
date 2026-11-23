import Foundation
import os

/// What the chat list shows, kept between launches.
struct ListSnapshot: Codable, Sendable, Equatable {
    var threads: [ThreadSummary]
    var agents: [AgentSummary]
    var savedAt: Double
    /// The cache's layout this was written in (`ChatCache.schema`); a file of another layout is dropped, not read.
    var schema: Int? = ChatCache.schema
}

/// What one chat keeps on disk: its newest runs and the tail of their stored events, so that the chat draws before
/// the network answers and then catches up by the events after the last one it holds.
struct StoredChat: Codable, Sendable, Equatable {
    /// How many stored events a chat keeps.
    static let tail: Int = 400

    var threadId: String
    var runs: [RunRecord]
    var helpers: [RunRecord]
    var events: [EventRecord]
    var savedAt: Double
    var schema: Int? = ChatCache.schema

    /// The chat's state: the runs oldest first, the newest two followed event by event, the older ones as their
    /// record has them.
    func state() -> ChatState {
        ChatState.build(runs: runs, helpers: helpers, events: events)
    }
}

extension ChatState {
    /// How many of the newest runs are drawn from their events (the rest from their records).
    static let followedRuns: Int = 2

    /// The state of a chat from its runs (any order), their helpers and the events held of them.
    static func build(runs: [RunRecord], helpers: [RunRecord], events: [EventRecord]) -> ChatState {
        var state: ChatState = ChatState()
        let roots: [RunRecord] = runs.filter { $0.parentRunId == nil }.sorted { $0.createdAt < $1.createdAt }
        let followed: Set<String> = Set(roots.suffix(followedRuns).map(\.id))
        let byRun: [String: [EventRecord]] = Dictionary(grouping: events, by: \.runId)
        for run in roots {
            if followed.contains(run.id) {
                state.addRun(run)
                state.apply((byRun[run.id] ?? []).sorted { $0.id < $1.id })
            } else {
                state.addSummary(run)
            }
        }
        state.setHelpers(helpers)
        if let newest: RunRecord = roots.last { state.setStatus(newest.status, ofRun: newest.id) }
        return state
    }
}

/// The app's store on disk: `list.json` and one `chat-<id>.json` each, under the app's Application Support folder in
/// a folder per daemon host. Writes are atomic and made off the main actor; a file that does not read is dropped (the
/// network fills it again), never shown half.
actor ChatCache {
    /// The layout of the files: raise it when a stored record changes shape, and what an older build wrote is read
    /// again from the daemon rather than drawn wrong.
    static let schema: Int = 1
    private let directory: URL
    private static let keptChats: Int = 40
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "cache")

    init(directory: URL) {
        self.directory = directory
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    /// The folder of a daemon host's cache.
    static func directory(for host: DaemonHost) -> URL {
        let base: URL = URL.applicationSupportDirectory.appending(path: "Dot/cache", directoryHint: .isDirectory)
        return base.appending(path: host.name.replacingOccurrences(of: ":", with: "_"), directoryHint: .isDirectory)
    }

    /// The chat list, read without waiting for an actor: the first frame of the app draws it.
    nonisolated static func readList(in directory: URL) -> ListSnapshot? {
        let url: URL = directory.appending(path: "list.json")
        guard let list: ListSnapshot = read(ListSnapshot.self, at: url) else { return nil }
        guard list.schema == schema else {
            try? FileManager.default.removeItem(at: url)
            return nil
        }
        return list
    }

    func loadList() -> ListSnapshot? { Self.readList(in: directory) }

    func saveList(_ snapshot: ListSnapshot) {
        write(snapshot, to: directory.appending(path: "list.json"))
    }

    func loadChat(_ id: String) -> StoredChat? {
        guard let chat: StoredChat = Self.read(StoredChat.self, at: file(for: id)) else { return nil }
        guard chat.schema == Self.schema else {
            try? FileManager.default.removeItem(at: file(for: id))
            return nil
        }
        return chat
    }

    func saveChat(_ chat: StoredChat) {
        var trimmed: StoredChat = chat
        let events: [EventRecord] = chat.events.sorted { $0.id < $1.id }
        trimmed.events = Array(events.suffix(StoredChat.tail)).map { $0.slimmed() }
        write(trimmed, to: file(for: chat.threadId))
    }

    func removeChat(_ id: String) {
        try? FileManager.default.removeItem(at: file(for: id))
    }

    /// Drops the chats that are not among `ids` (deleted on the Mac) and the oldest beyond the most kept.
    func prune(keeping ids: [String]) {
        let wanted: Set<String> = Set(ids.prefix(Self.keptChats))
        let urls: [URL] = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        for url in urls where url.lastPathComponent.hasPrefix("chat-") {
            let id: String = String(url.deletingPathExtension().lastPathComponent.dropFirst("chat-".count))
            if !wanted.contains(id) { try? FileManager.default.removeItem(at: url) }
        }
    }

    /// Forgets everything, as when the phone's key is forgotten.
    func clear() {
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    private func file(for id: String) -> URL {
        let safe: String = id.filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        return directory.appending(path: "chat-\(safe).json")
    }

    private func write(_ value: some Encodable, to url: URL) {
        do {
            try JSONEncoder().encode(value).write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch {
            logger.error("could not write \(url.lastPathComponent, privacy: .public): \(String(describing: error), privacy: .public)")
        }
    }

    nonisolated private static func read<T: Decodable>(_ type: T.Type, at url: URL) -> T? {
        guard let data: Data = try? Data(contentsOf: url) else { return nil }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            try? FileManager.default.removeItem(at: url)
            return nil
        }
    }
}
