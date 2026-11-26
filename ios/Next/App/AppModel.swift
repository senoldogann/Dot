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
    // The store's snapshot by part (item 1848): a screen is drawn again only when a part it reads changed, so a streamed word
    // redraws the open chat and not the chat list beneath it, the tab bar or Settings.
    private(set) var list: ListProjection = ListProjection()
    private(set) var chats: [String: ChatProjection] = [:]
    private(set) var outbox: [OutboxItem] = []
    private(set) var accepted: [String: AcceptedMessage] = [:]
    private(set) var transport: Transport = .connecting
    private(set) var online: Bool = true
    private(set) var savedAt: [String: Double] = [:]
    private(set) var workingAt: [String: Double] = [:]
    private(set) var startedChats: [String: String] = [:]
    /// The chats whose delete the daemon refused, with its words (item 1800).
    private(set) var refusedDeletes: [String: String] = [:]
    /// The Mac's Dot was reset and the owner has not closed the notice yet.
    private(set) var reset: DaemonReset?
    private(set) var host: DaemonHost?
    @ObservationIgnored private(set) var engine: Engine?
    @ObservationIgnored private var watching: Task<Void, Never>?
    @ObservationIgnored private let keys: PhoneKeyStore = PhoneKeyStore()
    @ObservationIgnored private let hosts: HostStore = HostStore()
    /// The Mac's name a debug fixture shows in place of a paired host's.
    private(set) var fixtureMac: String?
    /// The paired Mac's name, for a chat's subtitle ("Dot · <Mac>").
    var macName: String? { host?.name ?? fixtureMac }

    /// The daemon a Settings fixture serves from memory (debug builds; `nil` in the shipped app).
    private(set) var fixtureAPI: DaemonAPI?

    #if DEBUG
        /// Draws Settings on a fixture daemon (`SettingsFixture`): the pages read it through the real `DaemonAPI`.
        func showSettingsFixture(_ fixture: SettingsFixture) {
            var drawn: EngineSnapshot = .empty
            drawn.transport = fixture.transport
            drawn.online = fixture.transport != .offline
            show(drawn)
            fixtureMac = "MacBook Air"
            fixtureAPI = fixture.api()
            phase = fixture == .pairing || fixture == .pairingLink ? .unpaired : .ready
            if fixture == .pairingLink { pairingStartWaiting = .link(host: SettingsFixture.host, code: "7KQ2M9XD") }
        }

        /// Draws a fixture's snapshot with no engine (the screenshot harness and the UI tests).
        func showFixture(_ fixture: EngineSnapshot, mac: String) {
            show(fixture)
            fixtureMac = mac
            // The models and defaults the chip and the chat's settings read, from the Settings fixture's daemon.
            fixtureAPI = SettingsFixture.root.api()
            phase = .ready
        }
    #endif

    /// Takes the store's newest snapshot: each part is set only when it changed, so its readers alone are drawn again.
    private func show(_ next: EngineSnapshot) {
        if next.list != list { list = next.list }
        if next.chats != chats { chats = next.chats }
        if next.outbox != outbox { outbox = next.outbox }
        if next.accepted != accepted { accepted = next.accepted }
        if next.transport != transport { transport = next.transport }
        if next.online != online { online = next.online }
        if next.savedAt != savedAt { savedAt = next.savedAt }
        if next.workingAt != workingAt { workingAt = next.workingAt }
        if next.startedChats != startedChats { startedChats = next.startedChats }
        if next.refusedDeletes != refusedDeletes { refusedDeletes = next.refusedDeletes }
        if next.reset != reset { reset = next.reset }
        if let fresh: DaemonReset = next.reset, !fresh.taken, UserDefaults.standard.double(forKey: Self.rescuedKey) != fresh.at { rescue(fresh) }
    }

    /// The draft of the new chat's composer, kept like a chat's (a chat's id is the daemon's `thr_…`, never this).
    static let newChatDraft: String = "new-chat"

    /// The draft of a new chat with `agent` (`nil`: Dot), each bot its own (item 1914: one draft for every bot put the
    /// words half-written to one bot in a new chat with another, sent to it).
    static func newChatDraft(agent: String?) -> String { agent.map { "\(newChatDraft).\($0)" } ?? newChatDraft }

    /// The reset whose words were put in the new chat's draft (its `at`), kept with the draft so that a kill before the
    /// engine heard of it does not put them there twice.
    private static let rescuedKey: String = "resetRescuedAt"

    /// A reset left words unsent: they go into the new chat's draft, with the drafts of the old chats (whose chats are
    /// gone), so that nothing the owner wrote is lost in silence; then the engine is told they were taken.
    private func rescue(_ fresh: DaemonReset) {
        guard let engine else { return }
        let defaults: UserDefaults = UserDefaults.standard
        let newChatKey: String = "draft.\(Self.newChatDraft)"
        let oldKeys: [String] = defaults.dictionaryRepresentation().keys.filter { $0.hasPrefix("draft.") && !$0.hasPrefix(newChatKey) }.sorted()
        let oldDrafts: [String] = oldKeys.compactMap { defaults.string(forKey: $0) }
        keep(draft: DaemonRules.draft(keeping: draft(of: Self.newChatDraft), adding: fresh.words + oldDrafts), of: Self.newChatDraft)
        for key in oldKeys { defaults.removeObject(forKey: key) }
        defaults.set(fresh.at, forKey: Self.rescuedKey)
        logger.notice("a reset's words went to the new chat's draft words=\(fresh.words.count) drafts=\(oldDrafts.count)")
        Task { await engine.store.resetTaken() }
    }

    /// The owner closed the reset's notice.
    func resetSeen() {
        guard let engine else { return }
        Task { await engine.store.resetSeen() }
    }

    /// A chat's messages on their way, from the parts they come from (outbox, accepted, the chat, the link).
    func pending(in thread: String) -> [PendingMessage] {
        var parts: EngineSnapshot = .empty
        parts.chats = chats[thread].map { [thread: $0] } ?? [:]
        parts.outbox = outbox
        parts.accepted = accepted
        parts.online = online
        return parts.pending(in: thread)
    }

    /// New chats not yet in the list, from the outbox and the link.
    var pendingChats: [PendingMessage] {
        var parts: EngineSnapshot = .empty
        parts.outbox = outbox
        parts.online = online
        return parts.pendingChats
    }

    /// The models the daemon offers (`GET /api/models`), for the composer's chip.
    var models: [ProviderModels] = []
    /// The daemon's main model and approval mode (`GET /api/status`), what "Default" means in the chat's settings.
    var chatDefaults: ChatDefaults?
    /// Why the models could not be read, for the model picker to say with a retry; `nil` once they are.
    var modelsProblem: String?
    /// What the composer's pickers offer from the daemon: skills, agents, teams, accounts (PLAN §22).
    var palette: PaletteView?
    /// Dot and the owner's agents (`GET /api/agents`), the home's bots (PLAN §27); read when the home appears, on its pull
    /// to refresh and after a bot is made, renamed or deleted.
    var agents: [AgentSummary] = []
    /// Why the last read of the agents failed, for the home's banner; `nil` once one works.
    var agentsProblem: SettingsFailure?
    /// Why a new bot's first meeting did not start, by agent id, for its chat's banner (the bot was made).
    var welcomeProblems: [String: SettingsFailure] = [:]
    /// The bots the owner pinned to the top of the home, in their order (kept on this phone).
    var pinnedBots: [String] = UserDefaults.standard.stringArray(forKey: "pinnedBots") ?? [] {
        didSet { UserDefaults.standard.set(pinnedBots, forKey: "pinnedBots") }
    }
    /// The bots the owner muted: their unread chats are not counted (kept on this phone).
    var mutedBots: Set<String> = Set(UserDefaults.standard.stringArray(forKey: "mutedBots") ?? []) {
        didSet { UserDefaults.standard.set(Array(mutedBots), forKey: "mutedBots") }
    }
    var teams: [TeamSummary] = []
    /// How the asks this phone answered without the outbox stand (the secrets), by approval id.
    var askPhases: [String: AskPhase] = [:]
    /// The new chats this phone started in this session, by their outbox key: their first words (item 1849).
    var startingChats: [String: String] = [:]

    /// The first words of a chat this phone started, once the daemon named its thread: its title until the chat's own
    /// is read (item 1849).
    func firstWords(of thread: String) -> String? {
        startedChats.first { $0.value == thread }.flatMap { startingChats[$0.key] }
    }
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
                self.show(next)
                // At most one picture a display frame at 30 Hz: the store's stream keeps only the newest, so the words
                // that came meanwhile are drawn together, never dropped (item 1848).
                try? await Task.sleep(for: .milliseconds(33))
            }
        }
        Task { await engine.supervisor.start() }
    }

    /// A pairing the owner opened from a `dotnext://pair` link or a scanned code, waiting for their one tap.
    var pairingStartWaiting: PairingStart?

    /// Takes a link the system opened in the app: a pair link waits for the owner's tap; anything else is ignored.
    func open(url: URL) {
        guard let start: PairingStart = pairingStart(url.absoluteString) else {
            logger.warning("an unknown link was ignored scheme=\(url.scheme ?? "", privacy: .public) host=\(url.host() ?? "", privacy: .public)")
            return
        }
        pairingStartWaiting = start
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
        UserDefaults.standard.set(address.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "lastPairAddress")
        do {
            let key: String = try await DotEngine.pair(host: host, typedCode: code, session: DaemonAPI.makeSession(), keys: keys, hosts: hosts)
            pairingStartWaiting = nil
            phase = .loading
            start(host: host, key: key)
            return nil
        } catch {
            guard case .unreachable = error else { return .pairing(error) }
            // Which hop failed, so the screen says what to do (Tailscale off, the Mac asleep, Dot not running).
            let probe: DiagnosisProbe = DiagnosisProbe(
                link: LinkWatcher.system().state, health: await DaemonAPI(host: host, key: "", session: DaemonAPI.makeSession(), streamSession: DaemonAPI.makeSession()).probeHealth(),
                status: .notRun, proof: .notRun)
            if let (_, problem) = diagnose(probe).firstFailure, problem != .other(0) { return .reach(problem) }
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
        show(.empty)
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
    func send(_ text: String, thread: String, steerInto run: String?, files: [OutgoingAttachment], picks: MessagePicks) async -> String? {
        guard let engine else { return nil }
        do {
            if let run { return try await engine.steer(text, run: run, thread: thread, attachments: files) }
            return try await engine.send(text, thread: thread, attachments: files, picks: picks)
        } catch {
            // The message or its attachments could not be written to the outbox.
            logger.error("a message could not be kept thread=\(thread, privacy: .public) error=\(String(describing: error), privacy: .public)")
            return nil
        }
    }

    /// Starts a new chat through the outbox, with what was picked in its settings (PLAN §24) and with Dot (`agent` `nil`) or
    /// one of the owner's agents: its key, which the chat opens at once (item 1849) and swaps for the chat's own id once
    /// the daemon names it; `nil` when the message could not be kept.
    func startChat(_ text: String, files: [OutgoingAttachment], start: ChatStart, agent: String?) async -> String? {
        guard let engine else { return nil }
        do {
            let key: String = try await engine.startChat(text, attachments: files, start: start, agent: agent)
            startingChats[key] = text
            return key
        } catch {
            logger.error("a new chat could not be kept error=\(String(describing: error), privacy: .public)")
            return nil
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

    func cancelUpload(_ key: String) {
        guard let engine else { return }
        Task { await engine.outbox.cancelUpload(key) }
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

    /// The models the daemon offers and its defaults, read once per launch for the composer's chip and the chat's
    /// settings (each again on the next ask while it could not be read).
    /// A read that fails says why (`modelsProblem`), for the picker to show with its retry: the defaults' too, without
    /// which no row is marked chosen or Default.
    func loadModels() async {
        guard let api: DaemonAPI = settingsAPI else { return }
        let place: String = String(localized: "Settings › Models", comment: "Where on the Mac: the Models page")
        var problem: String?
        if models.isEmpty {
            do {
                models = try await api.models()
            } catch {
                logger.warning("the models could not be read error=\(String(describing: error), privacy: .public)")
                problem = failureWords(settingsFailure(error), macPlace: place)
            }
        }
        if chatDefaults == nil {
            do {
                let status: StatusView = try await api.fullStatus()
                chatDefaults = ChatDefaults(model: status.models.main, approvals: status.approvals)
            } catch {
                logger.warning("the daemon's defaults could not be read error=\(String(describing: error), privacy: .public)")
                problem = problem ?? failureWords(settingsFailure(error), macPlace: place)
            }
        }
        modelsProblem = problem
    }

    /// A model's name as the daemon gives it, else the reference's last part ("claude_code:opus" → "opus").
    func modelLabel(_ ref: String) -> String {
        models.flatMap(\.models).first { $0.ref == ref }?.label ?? String(ref.split(separator: ":").last ?? Substring(ref))
    }

    /// The pickers' lists, read once per launch; a list that cannot be read stays empty and says so in the log.
    func loadPalette() async {
        guard let engine, palette == nil else { return }
        do {
            palette = try await engine.api.palette()
            if agents.isEmpty { agents = try await engine.api.agents() }
            teams = try await engine.api.teams()
        } catch {
            logger.warning("the composer's lists could not be read error=\(String(describing: error), privacy: .public)")
        }
    }

    /// A side question (`/btw`): the run's answer, or why there is none.
    func aside(run: String, question: String) async -> String {
        guard let engine else { return String(localized: "Not paired with a Mac", comment: "A call that needs the Mac when this phone is not paired") }
        do {
            return try await engine.api.aside(run: run, question: question).answer
        } catch {
            return String(localized: "The side question could not be asked: \(String(describing: error))", comment: "Composer: /btw failed; the argument is why")
        }
    }

    /// A loop of the chat (`/loop`); `nil` when it was made, else why not.
    func loop(run: String, every: String, text: String) async -> String? {
        guard let engine else { return String(localized: "Not paired with a Mac", comment: "A call that needs the Mac when this phone is not paired") }
        do {
            try await engine.api.loop(run: run, every: every, text: text)
            return nil
        } catch {
            return String(describing: error)
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

    func actions(of run: String) async -> RunActions? {
        guard let engine else { return nil }
        return try? await engine.api.runActions(run: run)
    }

    func changes(of run: String) async -> RunChanges? {
        guard let engine else { return nil }
        return try? await engine.api.changes(run: run)
    }

    /// The owner's half-written words of a chat, kept across launches (per-chat drafts).
    /// A fixture's chat (debug builds) starts with no draft and keeps none, so a scene draws the same each time.
    func draft(of thread: String) -> String {
        fixtureMac != nil ? "" : UserDefaults.standard.string(forKey: "draft.\(thread)") ?? ""
    }

    func keep(draft: String, of thread: String) {
        guard fixtureMac == nil else { return }
        if draft.isEmpty { UserDefaults.standard.removeObject(forKey: "draft.\(thread)") } else { UserDefaults.standard.set(draft, forKey: "draft.\(thread)") }
    }
}

/// Why pairing did not work.
enum PairingProblem: Equatable {
    case address(DaemonHostError)
    case pairing(PairingFailure)
    /// The Mac could not be reached, and the probe found where.
    case reach(HopProblem)
}
