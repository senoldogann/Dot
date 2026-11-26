import Foundation

// The routes of a chat's message actions and of the run inspector's depth (package W4), each the one the web app calls
// for the same thing (`views/run.js`, `drafts.js`, `recap.js`, `cost.js`, `runchanges.js`,
// `background.js`). A call is made once; the screen that made it says the failure and offers the tap again.

private struct RewindBody: Encodable { let files: RewindFiles }
private struct RecapBody: Encodable { let force: Bool }
private struct UndoBody: Encodable { let path: String }

extension DaemonAPI {
    /// Edit from here (item 282): the chat goes back to before the run's message; `revert` takes back the files the
    /// runs taken out changed. The message's words and files come back for the composer.
    public func rewind(run: String, files: RewindFiles) async throws(DaemonRequestError) -> RewindView {
        try await send("POST", "/api/runs/\(run)/rewind", body: Self.json(RewindBody(files: files)))
    }

    /// A new run with the given words and choices (`POST /api/runs`): Retry's message sent again after its rewind.
    public func startRun(_ run: RetryRun) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/runs", body: Self.json(run))
    }

    /// Continue a run that was cut off, blocked or failed (`POST /api/runs/{id}/resume`): a new run goes on from where
    /// it stopped. 409 when it can no longer be resumed.
    public func resume(run: String) async throws(DaemonRequestError) -> RunRecord {
        try await send("POST", "/api/runs/\(run)/resume", body: nil)
    }

    /// The run as its page shows it (`GET /api/runs/{id}`): whether it can be resumed, its lessons.
    public func runDetail(run: String) async throws(DaemonRequestError) -> RunDetail { try await get("/api/runs/\(run)", query: []) }

    public func recap(run: String) async throws(DaemonRequestError) -> RecapView { try await get("/api/runs/\(run)/recap", query: []) }

    /// Writes a recap now (`force`), or when one is due; one being written is waited for.
    public func writeRecap(run: String, force: Bool) async throws(DaemonRequestError) -> RecapView {
        try await send("POST", "/api/runs/\(run)/recap", body: Self.json(RecapBody(force: force)))
    }

    public func costs(run: String) async throws(DaemonRequestError) -> RunCosts { try await get("/api/runs/\(run)/stats", query: []) }

    /// Takes back the runs' change to one file that is as they left it; the inspector's view afresh.
    public func undo(run: String, path: String) async throws(DaemonRequestError) -> RunActions {
        try await send("POST", "/api/runs/\(run)/undo", body: Self.json(UndoBody(path: path)))
    }

    /// The chat's terminal sessions (item 229).
    public func terminals(thread: String) async throws(DaemonRequestError) -> [TerminalView] {
        try await get("/api/threads/\(thread)/terminals", query: [])
    }

    /// Ends the program of one of the chat's terminal sessions and everything it started.
    public func closeTerminal(thread: String, session: String) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/threads/\(thread)/terminals/\(session)", body: nil)
    }
}
