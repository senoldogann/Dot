import Foundation

// The manager review of the team (item 2068, `routes_agents.py`): read it, choose the manager and how often, review
// now, and accept or skip a proposal.

extension DaemonAPI {
    public func teamReview() async throws(DaemonRequestError) -> TeamReviewView { try await get("/api/team-review", query: []) }

    public func setTeamReview(_ settings: TeamReviewSettings) async throws(DaemonRequestError) -> TeamReviewView {
        try await send("PUT", "/api/team-review", body: Self.json(settings))
    }

    /// Starts a review now (202); 409 while one is under way.
    public func reviewTeamNow() async throws(DaemonRequestError) -> TeamReviewView {
        try await send("POST", "/api/team-review/run", body: nil)
    }

    /// Makes the change a proposal proposes; 409 for one that no longer fits the team or was decided already.
    public func acceptTeamProposal(_ id: String) async throws(DaemonRequestError) -> TeamReviewView {
        try await send("POST", "/api/team-review/proposals/\(id)/accept", body: nil)
    }

    public func skipTeamProposal(_ id: String) async throws(DaemonRequestError) -> TeamReviewView {
        try await send("POST", "/api/team-review/proposals/\(id)/skip", body: nil)
    }
}
