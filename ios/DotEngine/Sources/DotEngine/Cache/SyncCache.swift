import Foundation
import os

/// What the cache keeps of a picture, saved together (plan §5, §9): the projection with its cursors, the daemon's
/// version, the schema and when it was saved, and when a run of it was still at work.
public struct CacheEnvelope<Content: Codable & Sendable>: Codable, Sendable {
    public let schema: Int
    public let daemonVersion: String
    public let savedAt: Double
    /// When the picture was saved while a run of it worked: the app says "was working at HH:MM".
    public let workingAt: Double?
    public let content: Content

    public init(schema: Int, daemonVersion: String, savedAt: Double, workingAt: Double?, content: Content) {
        self.schema = schema
        self.daemonVersion = daemonVersion
        self.savedAt = savedAt
        self.workingAt = workingAt
        self.content = content
    }
}

/// The cache's rules, pure.
public enum CacheRules {
    /// Bumped whenever what the cache holds changes shape.
    public static let schema: Int = 1
    /// A copy older than this is not shown.
    public static let maxAge: Double = 12 * 3600

    /// Whether a copy may be shown at launch, before the daemon is heard: the same schema, younger than 12 h.
    public static func showable(schema: Int, savedAt: Double, now: Double) -> Bool {
        schema == Self.schema && now - savedAt >= 0 && now - savedAt < maxAge
    }

    /// Whether a copy's cursors may be resumed from: showable, and saved from the same daemon version.
    public static func resumable(schema: Int, savedAt: Double, version: String, daemonVersion: String, now: Double) -> Bool {
        showable(schema: schema, savedAt: savedAt, now: now) && version == daemonVersion
    }
}

/// The cache on disk, under the engine's own `v2/` folder of DotNext's app group (plan §11: the old app's files are
/// never read or written here). One file per chat and one for the list, each written atomically.
public struct SyncCache: Sendable {
    public let folder: URL
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "cache")

    public init(folder: URL) {
        self.folder = folder
    }

    private var chats: URL { folder.appending(path: "chats", directoryHint: .isDirectory) }
    private var list: URL { folder.appending(path: "list.json") }

    /// A chat's file name: its id when it is one of the daemon's (`thr_...`), never a path.
    private func chatFile(_ thread: String) -> URL? {
        guard thread.wholeMatch(of: /[A-Za-z0-9_-]{1,80}/) != nil else { return nil }
        return chats.appending(path: "\(thread).json")
    }

    public func saveChat(_ chat: ChatProjection, version: String, now: Double) {
        guard let file: URL = chatFile(chat.threadId), chat.view != nil, chat.freshness != .deleted else { return }
        let working: Bool = chat.view?.runs.contains(where: \.isActive) ?? false
        write(CacheEnvelope(schema: CacheRules.schema, daemonVersion: version, savedAt: now, workingAt: working ? now : nil, content: chat), to: file)
    }

    public func loadChat(_ thread: String, now: Double) -> CacheEnvelope<ChatProjection>? {
        guard let file: URL = chatFile(thread) else { return nil }
        return read(file, now: now)
    }

    public func removeChat(_ thread: String) {
        guard let file: URL = chatFile(thread) else { return }
        try? FileManager.default.removeItem(at: file)
    }

    public func saveList(_ list: ListProjection, version: String, now: Double) {
        guard list.revision != nil else { return }
        write(CacheEnvelope(schema: CacheRules.schema, daemonVersion: version, savedAt: now, workingAt: nil, content: list), to: self.list)
    }

    public func loadList(now: Double) -> CacheEnvelope<ListProjection>? { read(list, now: now) }

    /// Forgets every copy (the phone's key was forgotten, or the daemon's version changed).
    public func clear() {
        try? FileManager.default.removeItem(at: folder)
    }

    private func write<Content: Codable & Sendable>(_ envelope: CacheEnvelope<Content>, to file: URL) {
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(envelope).write(to: file, options: [.atomic])
        } catch {
            logger.error("cache write failed file=\(file.lastPathComponent, privacy: .public) error=\(String(describing: error), privacy: .public)")
        }
    }

    /// A copy that may be shown; one that may not (another schema, too old, unreadable) is deleted, never kept.
    private func read<Content: Codable & Sendable>(_ file: URL, now: Double) -> CacheEnvelope<Content>? {
        guard let data: Data = try? Data(contentsOf: file) else { return nil }
        guard let envelope: CacheEnvelope<Content> = try? JSONDecoder().decode(CacheEnvelope<Content>.self, from: data),
            CacheRules.showable(schema: envelope.schema, savedAt: envelope.savedAt, now: now)
        else {
            try? FileManager.default.removeItem(at: file)
            return nil
        }
        return envelope
    }
}
