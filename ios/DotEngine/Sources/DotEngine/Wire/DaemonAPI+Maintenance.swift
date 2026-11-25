import Foundation

// The routes of Settings › System › Maintenance (item 1790).

private struct ResumeBody: Encodable { let resume: String }

extension DaemonAPI {
    /// The provider programs on the Mac and their versions; a daemon from before item 1790 answers 404.
    public func programs() async throws(DaemonRequestError) -> ProgramsView { try await get("/api/diagnostics/programs", query: []) }

    /// Whether the daemon's next start resumes on its own the runs it cut off (`safe`) or leaves them to the owner
    /// (`off`): `[agent] resume`.
    public func setResume(_ resume: String) async throws(DaemonRequestError) -> ResumeConfig {
        try await send("PUT", "/api/agent/resume", body: Self.json(ResumeBody(resume: resume)))
    }
}
