import Foundation

/// One daemon's engine, made whole: the API, the store, the outbox and the supervisor that owns them (plan §5).
public struct Engine: Sendable {
    public let api: DaemonAPI
    public let store: SyncStore
    public let outbox: Outbox
    public let supervisor: Supervisor
    public let link: LinkWatcher

    /// `folder` is the engine's `v2/` folder (the app group's in the app, a scratch folder in the tests).
    public init(api: DaemonAPI, folder: URL, link: LinkWatcher, clock: any EngineClock, jitter: @escaping @Sendable () -> Double) {
        let store: SyncStore = SyncStore(cache: SyncCache(folder: folder.appending(path: "cache", directoryHint: .isDirectory)), clock: clock)
        let outbox: Outbox = Outbox(
            directory: folder.appending(path: "outbox", directoryHint: .isDirectory), api: api, link: link, clock: clock,
            policy: .delivery, jitter: jitter, mayStartChat: { await store.listIsLive })
        self.api = api
        self.store = store
        self.outbox = outbox
        self.link = link
        self.supervisor = Supervisor(api: api, store: store, outbox: outbox, link: link, clock: clock, jitter: jitter)
    }

    /// The `v2/` folder of DotNext's app group; `nil` when the group is not there (a build without the entitlement).
    public static func groupFolder() -> URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: nextAppGroup)?.appending(path: "v2", directoryHint: .isDirectory)
    }

    // MARK: Doings (through the outbox)

    /// Sends the owner's words to a chat as the D10 default: queued behind the run at work, or started when none works.
    public func send(_ text: String, thread: String, photos: [Data], picks: MessagePicks) async throws -> String {
        let key: String = newMessageKey()
        try await outbox.enqueue(.message(thread: thread, text: text, images: [], uploaded: [], picks: picks), id: key, images: photos)
        return key
    }

    /// Sends the owner's words into the run at work (steer).
    public func steer(_ text: String, run: String, thread: String, photos: [Data]) async throws -> String {
        let key: String = newMessageKey()
        try await outbox.enqueue(.steer(run: run, thread: thread, text: text, images: [], uploaded: []), id: key, images: photos)
        return key
    }

    public func startChat(_ text: String, photos: [Data]) async throws -> String {
        let key: String = newMessageKey()
        try await outbox.enqueue(.newChat(text: text, images: [], uploaded: []), id: key, images: photos)
        return key
    }

    /// A doing that says the same twice (stop, queue change, rename, delete, an answer).
    public func perform(_ action: OutboxItem.Action) async throws {
        try await outbox.enqueue(action, id: newMessageKey(), images: [])
    }

    /// Gives a secret a card asks for: sent at once over the key, never written to the outbox's file (a secret is kept
    /// nowhere on the phone). A failure is thrown for the card to show.
    public func giveSecret(approval: String, value: String) async throws(DaemonRequestError) -> ApprovalRecord {
        try await api.giveSecret(approval: approval, value: value)
    }
}
