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

extension DaemonRequestError {
    /// The Mac refused these reports for good (a 4xx that a retry or a later look will not change, but for the answers
    /// that mean the key or an old daemon: 401, 403, 404): the batch is dropped, not kept to block the newer ones.
    public var refusesTheReportsForGood: Bool {
        guard case .status(_, let code, _) = self else { return false }
        return (400..<500).contains(code) && ![401, 403, 404, 408, 429].contains(code)
    }
}
