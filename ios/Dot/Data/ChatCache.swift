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

/// What the screens beside the chats keep between launches: the models (the default, the review model and every
/// provider's), and the schedules.
struct ExtrasSnapshot: Codable, Sendable, Equatable {
    var models: ModelChoices?
    var providers: [ProviderModels]?
    var schedules: [ScheduleView]?
    var savedAt: Double
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
    /// How many events of each run did not read, by run id: the chat keeps saying so.
    var unreadable: UnreadableEvents?
    /// What stands beside the transcript, so that a chat opened with no link shows its queue, its schedules and its
    /// reactions as they were (item 1371); a file written before has none.
    var queue: QueueView?
    var automations: [ThreadAutomation]?
    var reactions: [ReactionRecord]?
    /// The chat row's `updated_at` this copy was read up to (the list's own clock, not the phone's): a prefetch reads a
    /// chat again only when the list says it moved on.
    var listUpdatedAt: Double?

    /// The chat's state: the runs oldest first, the newest two followed event by event, the older ones as their
    /// record has them.
    func state() -> ChatState {
        var built: ChatState = ChatState.build(runs: runs, helpers: helpers, events: events)
        for (run, ids) in unreadable?.byRun ?? [:] { built.setUnreadable(ids.count, ofRun: run) }
        return built
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

/// The app's store on disk: `list.json`, `extras.json` and one `chat-<id>.json` each, under the app's Application Support
/// folder in a folder per daemon host. A write is atomic (a temporary file renamed over the old one: a crash leaves the
/// old file or the new, never half) and made off the main actor, and a file whose bytes did not change is not written
/// again. A file that does not read, or was written in a layout this build does not know, is dropped (the network fills
/// it again), never shown half. The folder is kept within `byteLimit`: past it the chats read longest ago go first.
actor ChatCache {
    /// The layout the files are written in: raise it when a stored record changes shape. A build reads the layouts
    /// `1...schema` (a record added a field that a file of an older layout lacks: it reads as none) and drops a newer
    /// one, which is then read again from the daemon rather than drawn wrong.
    static let schema: Int = 2
    /// How many chats and how many bytes the folder keeps (item 1371).
    static let keptChats: Int = 60
    static let byteLimit: Int = 24 * 1024 * 1024
    private let directory: URL
    private var written: [String: Int] = [:]
    private var lastCompaction: Date = .distantPast
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

    nonisolated static func isReadable(schema value: Int?) -> Bool {
        guard let value else { return true }
        return (1...schema).contains(value)
    }

    /// The chat list, read without waiting for an actor: the first frame of the app draws it.
    nonisolated static func readList(in directory: URL) -> ListSnapshot? {
        let url: URL = directory.appending(path: "list.json")
        guard let list: ListSnapshot = read(ListSnapshot.self, at: url) else { return nil }
        guard isReadable(schema: list.schema) else {
            try? FileManager.default.removeItem(at: url)
            return nil
        }
        return list
    }

    /// A chat read without waiting for the actor (the preload decodes several at once, off the main actor).
    nonisolated static func readChat(_ id: String, in directory: URL) -> StoredChat? {
        let url: URL = file(for: id, in: directory)
        guard let chat: StoredChat = read(StoredChat.self, at: url) else { return nil }
        guard isReadable(schema: chat.schema) else {
            try? FileManager.default.removeItem(at: url)
            return nil
        }
        touch(url)
        return chat
    }

    func loadList() -> ListSnapshot? { Self.readList(in: directory) }

    func saveList(_ snapshot: ListSnapshot) {
        var current: ListSnapshot = snapshot
        current.schema = Self.schema
        write(current, to: directory.appending(path: "list.json"))
    }

    func loadChat(_ id: String) -> StoredChat? { Self.readChat(id, in: directory) }

    func saveChat(_ chat: StoredChat) {
        var trimmed: StoredChat = chat
        let events: [EventRecord] = chat.events.sorted { $0.id < $1.id }
        trimmed.events = Array(events.suffix(StoredChat.tail)).map { $0.slimmed() }
        trimmed.schema = Self.schema
        write(trimmed, to: Self.file(for: chat.threadId, in: directory))
        compactIfDue()
    }

    func removeChat(_ id: String) {
        try? FileManager.default.removeItem(at: Self.file(for: id, in: directory))
        written[Self.file(for: id, in: directory).lastPathComponent] = nil
    }

    nonisolated static func readExtras(in directory: URL) -> ExtrasSnapshot? {
        let url: URL = directory.appending(path: "extras.json")
        guard let extras: ExtrasSnapshot = read(ExtrasSnapshot.self, at: url) else { return nil }
        guard isReadable(schema: extras.schema) else {
            try? FileManager.default.removeItem(at: url)
            return nil
        }
        return extras
    }

    func loadExtras() -> ExtrasSnapshot? { Self.readExtras(in: directory) }

    func saveExtras(_ extras: ExtrasSnapshot) {
        var current: ExtrasSnapshot = extras
        current.schema = Self.schema
        write(current, to: directory.appending(path: "extras.json"))
    }

    /// Drops the chats that are not among `ids` (deleted on the Mac), the oldest beyond the most kept, and, when the
    /// folder is over its size, the chats read longest ago.
    func prune(keeping ids: [String]) {
        let wanted: Set<String> = Set(ids.prefix(Self.keptChats))
        for url in chatFiles() {
            let id: String = String(url.deletingPathExtension().lastPathComponent.dropFirst("chat-".count))
            if !wanted.contains(id) {
                try? FileManager.default.removeItem(at: url)
                written[url.lastPathComponent] = nil
            }
        }
        compact(limit: Self.byteLimit)
    }

    /// What the cache takes on disk, in bytes (every file of the folder).
    func size() -> Int {
        let urls: [URL] = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        return urls.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
    }

    /// Brings the chat files under `limit` bytes by removing the chats read or written longest ago first (the file's
    /// modification time is the clock: a read touches it). The list and the extras are never removed. Returns how many
    /// chats went.
    @discardableResult
    func compact(limit: Int) -> Int {
        var entries: [(url: URL, size: Int, used: Date)] = chatFiles().map { url in
            let values: URLResourceValues? = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
            return (url, values?.fileSize ?? 0, values?.contentModificationDate ?? .distantPast)
        }
        let others: Int = size() - entries.reduce(0) { $0 + $1.size }
        var total: Int = others + entries.reduce(0) { $0 + $1.size }
        guard total > limit else { return 0 }
        entries.sort { $0.used < $1.used }
        var removed: Int = 0
        for entry in entries where total > limit {
            try? FileManager.default.removeItem(at: entry.url)
            written[entry.url.lastPathComponent] = nil
            total -= entry.size
            removed += 1
        }
        logger.info("the cache was over \(limit) bytes: \(removed) chats were let go")
        return removed
    }

    private func compactIfDue() {
        guard Date().timeIntervalSince(lastCompaction) > 30 else { return }
        lastCompaction = Date()
        compact(limit: Self.byteLimit)
    }

    /// Forgets everything, as when the phone's key is forgotten.
    func clear() {
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        written = [:]
    }

    private func chatFiles() -> [URL] {
        let urls: [URL] = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return urls.filter { $0.lastPathComponent.hasPrefix("chat-") && $0.pathExtension == "json" }
    }

    nonisolated private static func file(for id: String, in directory: URL) -> URL {
        let safe: String = id.filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        return directory.appending(path: "chat-\(safe).json")
    }

    nonisolated private static func touch(_ url: URL) {
        try? FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: url.path)
    }

    private func write(_ value: some Encodable, to url: URL) {
        do {
            // Sorted keys: the same chat is the same bytes, so that a file that did not change is not written again.
            let encoder: JSONEncoder = JSONEncoder()
            encoder.outputFormatting = .sortedKeys
            let data: Data = try encoder.encode(value)
            let fingerprint: Int = data.hashValue
            // Identical bytes are not written again: a chat saved at every burst of words writes what changed only.
            if written[url.lastPathComponent] == fingerprint, FileManager.default.fileExists(atPath: url.path) { return }
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            written[url.lastPathComponent] = fingerprint
        } catch {
            logger.error("could not write \(url.lastPathComponent, privacy: .public): \(String(describing: error), privacy: .public)")
        }
    }

    nonisolated private static func read<T: Decodable>(_ type: T.Type, at url: URL) -> T? {
        guard let data: Data = try? Data(contentsOf: url) else { return nil }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            // Bytes that are not a record of this build (a write cut short on a full disk, a layout of another version):
            // dropped, and read from the daemon again.
            try? FileManager.default.removeItem(at: url)
            return nil
        }
    }
}
