import DotEngine
import Foundation

// The Activity tab's part of the app's adapter (item 1826). Its reads and writes go through Settings' `read` and
// `write` (`SettingsKit.swift`): the same API, the same failure words.

extension AppModel {
    /// The chat a run is in: the chat at the top of a delegated run's chain when the row names it, else the run's own
    /// (`GET /api/runs/{id}`); why not, when it cannot be read.
    func chat(ofRun run: String, rootThread: String?) async -> Result<String, SettingsFailureBox> {
        if let rootThread { return .success(rootThread) }
        return await write { api throws(DaemonRequestError) in try await api.runHead(run).run.threadId }
    }

    /// The moment "while you were away" reads from: the one any device marked, or, on the first visit from any device,
    /// now (marked for every device), so that the card never pours out history (the web's `away.js`).
    func awayMoment() async -> Loadable<Double> {
        switch await read({ api throws(DaemonRequestError) in try await api.awayRead() }) {
        case .loaded(let marked):
            if let at = marked.readAt { return .loaded(at) }
            return await read { api throws(DaemonRequestError) in
                try await api.markAwayRead(until: Date().timeIntervalSince1970).readAt ?? Date().timeIntervalSince1970
            }
        case .loading: return .loading
        case .failed(let failure): return .failed(failure)
        }
    }
}

extension AppModel {
    /// The moment any device last marked "while you were away" read, without marking it: `nil` before any did (an
    /// agent's profile, which shows a part of the card, never writes the Dot-wide moment).
    func awayMarked() async -> Loadable<Double?> {
        switch await read({ api throws(DaemonRequestError) in try await api.awayRead() }) {
        case .loaded(let marked): return .loaded(marked.readAt)
        case .loading: return .loading
        case .failed(let failure): return .failed(failure)
        }
    }
}

/// How a Send or a Discard of a draft went.
enum DraftOutcome: Equatable {
    /// The daemon took it: the draft as it stands now (`sent`, `discarded`, or `failed` with the application's words).
    case answered(DraftView)
    /// 409: it no longer waits (sent or discarded on the Mac or another screen).
    case answeredElsewhere
    case failed(SettingsFailure)
}

extension AppModel {
    /// Sends or discards a draft with `call`; a 409 is told apart, as the draft was answered elsewhere.
    func settleDraft(_ call: (DaemonAPI) async throws(DaemonRequestError) -> DraftView) async -> DraftOutcome {
        guard let api: DaemonAPI = settingsAPI else { return .failed(.unreachable) }
        do throws(DaemonRequestError) {
            return .answered(try await call(api))
        } catch {
            logger.warning("a draft could not be settled error=\(String(describing: error), privacy: .public)")
            return error.statusCode == 409 ? .answeredElsewhere : .failed(settingsFailure(error))
        }
    }
}
