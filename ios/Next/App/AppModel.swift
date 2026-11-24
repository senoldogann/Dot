import DotEngine
import Foundation
import Observation
import SwiftUI
import UIKit
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
    /// The Mac's name a debug fixture shows in place of a paired host's.
    private(set) var fixtureMac: String?
    /// The paired Mac's name, for a chat's subtitle ("Dot · <Mac>").
    var macName: String? { host?.name ?? fixtureMac }

    #if DEBUG
        /// Draws a fixture's snapshot with no engine (the screenshot harness and the UI tests).
        func showFixture(_ fixture: EngineSnapshot, mac: String) {
            snapshot = fixture
            fixtureMac = mac
            phase = .ready
        }
    #endif

    /// The models the daemon offers (`GET /api/models`), for the composer's chip.
    var models: [ProviderModels] = []
    /// How the asks this phone answered without the outbox stand (the secrets), by approval id.
    var askPhases: [String: AskPhase] = [:]
    /// The asks this phone answered, kept so the transcript can say "by you on this phone".
    var answeredHere: Set<String> = Set(UserDefaults.standard.stringArray(forKey: "answeredHere") ?? []) {
        didSet { UserDefaults.standard.set(Array(answeredHere.suffix(300)), forKey: "answeredHere") }
    }
    @ObservationIgnored let images: NSCache<NSString, UIImage> = NSCache()
    /// Debug builds only: the time each probe message was sent, by its client key (`LatencyProbe`).
    @ObservationIgnored var probeSent: [String: Date] = [:]
    @ObservationIgnored let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "app")

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
    func send(_ text: String, thread: String, steerInto run: String?, photos: [Data]) async -> String? {
        guard let engine else { return nil }
        do {
            if let run { return try await engine.steer(text, run: run, thread: thread, photos: photos) }
            return try await engine.send(text, thread: thread, photos: photos)
        } catch {
            // Only writing the message's photos beside the outbox can fail here.
            logger.error("a message could not be kept thread=\(thread, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return nil
        }
    }

    func startChat(_ text: String, photos: [Data]) async {
        guard let engine else { return }
        do {
            _ = try await engine.startChat(text, photos: photos)
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

// MARK: The chat (wave N2)

extension AppModel {
    /// Answers an ask through the outbox (an answer says the same twice, plan §11) and remembers that this phone
    /// answered it, for the transcript's "by you on this phone".
    func decide(_ ask: Ask, approve: Bool, note: String, remember: Bool, thread: String) {
        answeredHere.insert(ask.id)
        perform(.decision(approval: ask.id, run: ask.runId, thread: thread, approve: approve, note: note, remember: remember))
    }

    /// Gives a secret at once (never through the outbox's file: a secret is kept nowhere on the phone).
    func giveSecret(_ ask: Ask, value: String) {
        guard let engine else { return }
        askPhases[ask.id] = .submitting
        answeredHere.insert(ask.id)
        Task {
            do throws(DaemonRequestError) {
                _ = try await engine.giveSecret(approval: ask.id, value: value)
            } catch {
                if error.statusCode == 409 || error.statusCode == 404 {
                    askPhases[ask.id] = .answeredElsewhere
                } else if let code = error.statusCode {
                    askPhases[ask.id] = .refused("HTTP \(code)")
                } else {
                    askPhases[ask.id] = .retry(String(describing: error))
                }
            }
        }
    }

    /// The models the daemon offers, read once per launch for the composer's chip.
    func loadModels() async {
        guard let engine, models.isEmpty else { return }
        do {
            models = try await engine.api.models()
        } catch {
            logger.warning("the models could not be read error=\(String(describing: error), privacy: .public)")
        }
    }

    /// A screenshot a tool returned, kept in memory for the session.
    func image(named name: String) async -> UIImage? {
        if let held: UIImage = images.object(forKey: name as NSString) { return held }
        guard let engine else { return nil }
        do {
            let data: Data = try await engine.api.image(named: name)
            guard let image: UIImage = UIImage(data: data) else { return nil }
            images.setObject(image, forKey: name as NSString)
            return image
        } catch {
            logger.warning("a screenshot could not be read name=\(name, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return nil
        }
    }

    /// The emergency stop (Settings, the daemon's failsafe): every run stops. `nil` when it worked, else why not.
    func emergencyStop() async -> String? {
        guard let engine else { return String(localized: "Not paired with a Mac", comment: "Emergency stop: no Mac to stop") }
        do {
            _ = try await engine.api.emergencyStop()
            return nil
        } catch {
            return String(describing: error)
        }
    }

    func actions(of run: String) async -> RunActions? {
        guard let engine else { return nil }
        return try? await engine.api.runActions(run: run)
    }

    func changes(of run: String) async -> RunChanges? {
        guard let engine else { return nil }
        return try? await engine.api.changes(run: run)
    }

    /// The owner's half-written words of a chat, kept across launches (per-chat drafts).
    func draft(of thread: String) -> String { UserDefaults.standard.string(forKey: "draft.\(thread)") ?? "" }

    func keep(draft: String, of thread: String) {
        if draft.isEmpty { UserDefaults.standard.removeObject(forKey: "draft.\(thread)") } else { UserDefaults.standard.set(draft, forKey: "draft.\(thread)") }
    }
}

/// Why pairing did not work.
enum PairingProblem: Equatable {
    case address(DaemonHostError)
    case pairing(PairingFailure)
}
