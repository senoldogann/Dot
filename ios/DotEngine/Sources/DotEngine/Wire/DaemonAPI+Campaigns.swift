import Foundation

// The campaign routes Settings › Agents › Campaigns reads and writes (item 1895), as the web's `campaigns.js` calls
// them. Each call is made once; the page that made it shows the failure and its Retry.

extension DaemonAPI {
    public func campaigns() async throws(DaemonRequestError) -> [CampaignSummary] { try await get("/api/campaigns", query: []) }

    public func campaign(_ id: String) async throws(DaemonRequestError) -> CampaignDetail { try await get("/api/campaigns/\(id)", query: []) }

    /// Stop (no session starts, the working one stops), resume, or close it as achieved; 409 when it already stands so
    /// (`CampaignRecord.actions` names what it takes).
    public func actOnCampaign(_ id: String, _ action: CampaignAction) async throws(DaemonRequestError) -> CampaignDetail {
        try await send("POST", "/api/campaigns/\(id)/\(action.rawValue)", body: nil)
    }
}
