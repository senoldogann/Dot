import Foundation

// The phone's own performance record (item 1926).

extension DaemonAPI {
    /// Sends reports of this phone's own performance (at most `PhoneReports.most`); only a phone's key may.
    public func sendMetrics(_ reports: [PhoneReport]) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/phone/metrics", body: Self.json(PhoneReports(reports: reports)))
    }

    /// The summary of this phone's last seven days; a daemon from before item 1926 answers 404.
    public func metricsSummary() async throws(DaemonRequestError) -> PhoneMetricsSummary { try await get("/api/phone/metrics", query: []) }
}
