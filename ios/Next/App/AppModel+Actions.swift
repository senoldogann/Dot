import DotEngine
import Foundation

/// What Retry did: the message went again as a new run, or the rewind happened and the send failed, so its words wait
/// in the composer (the web's `retry`: the message is never lost between the two calls).
enum RetryOutcome: Sendable {
    case sent(RunRecord)
    case leftInComposer(RewindView, SettingsFailure)
}

// The doings behind a chat's message actions and the run inspector's depth (package W4). Each is the owner's own tap,
// made once on the route the web uses; the screen says the result or the failure (`failureWords`) and offers the tap
// again. They go to the paired Mac's daemon (or a debug fixture's), as Settings' reads and writes do.
extension AppModel {
    /// Edit from here (item 282): the chat goes back to before the run's message; the words come back for the composer.
    func editFromHere(run: String, files: RewindFiles) async -> Result<RewindView, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.rewind(run: run, files: files) }
    }

    /// Retry (item 1185): the rewind, then the message again with the chat's choices, in the chat the rewind left.
    func retry(run: RunRecord, files: RewindFiles, choices: ChatChoices?) async -> Result<RetryOutcome, SettingsFailureBox> {
        switch await editFromHere(run: run.id, files: files) {
        case .failure(let failure):
            return .failure(failure)
        case .success(let cut):
            let ref: String = choices?.model ?? run.model
            let efforts: [String] = models.flatMap(\.models).first { $0.ref == ref }?.efforts ?? []
            let body: RetryRun = retryRun(cut: cut, choices: choices, past: run, efforts: efforts, key: UUID().uuidString)
            switch await write({ api throws(DaemonRequestError) in try await api.startRun(body) }) {
            case .success(let started): return .success(.sent(started))
            case .failure(let failure): return .success(.leftInComposer(cut, failure.failure))
            }
        }
    }

    /// Continue a run that was cut off, blocked or failed: a new run goes on from where it stopped.
    func resume(run: String) async -> Result<RunRecord, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.resume(run: run) }
    }

    func recap(of run: String) async -> Loadable<RecapView> {
        await read { api throws(DaemonRequestError) in try await api.recap(run: run) }
    }

    func writeRecap(of run: String) async -> Result<RecapView, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.writeRecap(run: run, force: true) }
    }

    func costs(of run: String) async -> Loadable<RunCosts> {
        await read { api throws(DaemonRequestError) in try await api.costs(run: run) }
    }

    func undo(run: String, path: String) async -> Result<RunActions, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.undo(run: run, path: path) }
    }

    func draft(_ id: String) async -> Loadable<DraftView> {
        await read { api throws(DaemonRequestError) in try await api.draft(id) }
    }

    func sendDraft(_ id: String, words: DraftWords) async -> Result<DraftView, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.sendDraft(id, words: words) }
    }

    func discardDraft(_ id: String) async -> Result<DraftView, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.discardDraft(id) }
    }

    func terminals(of thread: String) async -> Loadable<[TerminalView]> {
        await read { api throws(DaemonRequestError) in try await api.terminals(thread: thread) }
    }

    func closeTerminal(thread: String, session: String) async -> Result<Void, SettingsFailureBox> {
        await write { api throws(DaemonRequestError) in try await api.closeTerminal(thread: thread, session: session) }
    }
}
