import DotEngine
import Foundation
import Observation
import SwiftUI
import os

/// The app's one adapter to the engine (plan §5): it holds the engine of the paired daemon, mirrors the store's snapshot
/// on the main actor for the screens, and turns the scene's phases and the owner's doings into the engine's calls.
@MainActor
@Observable
final class AppModel {
    enum Phase: Equatable {
        /// The key and the address are being read.
        case loading
        /// No key: the pairing screen.
        case unpaired
        case ready
        /// DotNext's own app group is not there (a build signed without it): nothing can be kept, so nothing starts.
        case noStorage
    }

    private(set) var phase: Phase = .loading
    private(set) var snapshot: EngineSnapshot = .empty
    private(set) var host: DaemonHost?
    @ObservationIgnored private(set) var engine: Engine?
    @ObservationIgnored private var watching: Task<Void, Never>?
    @ObservationIgnored private let keys: PhoneKeyStore = PhoneKeyStore()
    @ObservationIgnored private let hosts: HostStore = HostStore()
    /// Debug builds only: the time each probe message was sent, by its client key (`LatencyProbe`).
    @ObservationIgnored var probeSent: [String: Date] = [:]
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "app")

    init() {}

    /// Reads the key and the address and starts the engine when both are there.
    func load() {
        guard phase == .loading else { return }
        #if DEBUG
            if let address: String = UserDefaults.standard.string(forKey: "DotNextHost"),
                let key: String = UserDefaults.standard.string(forKey: "DotNextKey"),
                let host: DaemonHost = parseLoopbackDaemonHost(address) ?? (try? parseDaemonHost(address, buildHost: nil).get())
            {
                start(host: host, key: key)
                return
            }
        #endif
        let saved: String?
        do {
            saved = try keys.read()
        } catch {
            saved = nil
        }
        guard let key: String = saved, let host: DaemonHost = hosts.load(loopbackAllowed: Self.loopbackAllowed) else {
            phase = .unpaired
            return
        }
        start(host: host, key: key)
    }

    static var loopbackAllowed: Bool {
        #if DEBUG
            true
        #else
            false
        #endif
    }

    private func start(host: DaemonHost, key: String) {
        guard let folder: URL = Engine.groupFolder() else {
            phase = .noStorage
            return
        }
        let api: DaemonAPI = DaemonAPI(host: host, key: key, session: DaemonAPI.makeSession(), streamSession: DaemonAPI.makeStreamSession())
        let engine: Engine = Engine(api: api, folder: folder, link: LinkWatcher.system(), clock: SystemClock(), jitter: { Double.random(in: 0...1) })
        self.engine = engine
        self.host = host
        phase = .ready
        watching = Task { [weak self] in
            for await next in await engine.store.updates() {
                guard let self else { return }
                if next != self.snapshot { self.snapshot = next }
            }
        }
        Task { await engine.supervisor.start() }
    }

    /// Pairs with the address and the typed code; `nil` when it worked.
    func pair(address: String, code: String) async -> PairingProblem? {
        let parsed: DaemonHost?
        if Self.loopbackAllowed, let local: DaemonHost = parseLoopbackDaemonHost(address) {
            parsed = local
        } else {
            switch parseDaemonHost(address, buildHost: nil) {
            case .success(let host): parsed = host
            case .failure(let failure): return .address(failure)
            }
        }
        guard let host: DaemonHost = parsed else { return .address(.malformed) }
        do {
            let key: String = try await DotEngine.pair(host: host, typedCode: code, session: DaemonAPI.makeSession(), keys: keys, hosts: hosts)
            phase = .loading
            start(host: host, key: key)
            return nil
        } catch {
            return .pairing(error)
        }
    }

    /// Forgets the key, the address, the outbox and the cache (Settings › This phone's key).
    func forget() async {
        guard let engine else { return }
        await engine.supervisor.stop()
        await engine.outbox.clear()
        await engine.store.clear()
        watching?.cancel()
        do {
            try keys.delete()
        } catch {
            // The key stays in the Keychain; the next pairing replaces it.
        }
        hosts.forget()
        self.engine = nil
        snapshot = .empty
        phase = .unpaired
    }

    // MARK: The scene

    func scene(_ phase: ScenePhase) {
        guard let engine else { return }
        switch phase {
        case .active: Task { await engine.supervisor.enterForeground() }
        case .background: Task { await engine.supervisor.enterBackground() }
        default: break
        }
    }

    // MARK: Chats

    func open(_ thread: String) {
        guard let engine else { return }
        Task { await engine.supervisor.openChat(thread) }
    }

    func close(_ thread: String) {
        guard let engine else { return }
        Task { await engine.supervisor.closeChat(thread) }
    }

    /// The owner's words for a chat: queued behind the run at work (D10 default) or, with `steer`, into it.
    @discardableResult
    func send(_ text: String, thread: String, steerInto run: String?) async -> String? {
        guard let engine else { return nil }
        do {
            if let run { return try await engine.steer(text, run: run, thread: thread, photos: []) }
            return try await engine.send(text, thread: thread, photos: [])
        } catch {
            // Only writing the message's photos beside the outbox can fail here.
            logger.error("a message could not be kept thread=\(thread, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return nil
        }
    }

    func startChat(_ text: String) async {
        guard let engine else { return }
        do {
            _ = try await engine.startChat(text, photos: [])
        } catch {
            logger.error("a new chat could not be kept error=\(String(describing: error), privacy: .public)")
        }
    }

    func perform(_ action: OutboxItem.Action) {
        guard let engine else { return }
        Task { [logger] in
            do {
                try await engine.perform(action)
            } catch {
                logger.error("a doing could not be kept error=\(String(describing: error), privacy: .public)")
            }
        }
    }

    func retry(_ key: String) {
        guard let engine else { return }
        Task { await engine.outbox.retry(key) }
    }

    func sendAsMessage(_ key: String) {
        guard let engine else { return }
        Task { await engine.outbox.sendAsMessage(key) }
    }

    /// Takes a refused message back for the composer: its words.
    func takeBack(_ key: String) async -> String? {
        guard let engine else { return nil }
        return await engine.outbox.discard(key)?.item.text
    }
}

/// Why pairing did not work.
enum PairingProblem: Equatable {
    case address(DaemonHostError)
    case pairing(PairingFailure)
}
