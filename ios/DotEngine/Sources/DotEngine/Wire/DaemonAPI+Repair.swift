import Foundation

public struct RepairStatus: Decodable, Sendable {
    public let repo: String
    public let running: Bool
    public let worker: RepairWorker
    public let workerLost: Bool
    public let problems: [RepairProblem]
    public let live: RepairLive?

    private enum CodingKeys: String, CodingKey {
        case repo, running, worker, problems, live
        case workerLost = "worker_lost"
    }
}

public struct RepairProblem: Decodable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let evidence: [String]
    public let count: Int
    public let note: String
    public let status: Status
    public let prURL: String

    public enum Status: String, Decodable, Sendable {
        case open, working, fixed, waiting, declined, closed
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, evidence, count, note, status
        case prURL = "pr_url"
    }
}

public struct RepairLive: Decodable, Sendable {
    public let title: String
    public let phase: Phase

    public enum Phase: String, Decodable, Sendable {
        case working, review, checks
    }
}

extension DaemonAPI {
    public func repairStatus() async throws(DaemonRequestError) -> RepairStatus {
        try await get("/api/repair", query: [])
    }

    public func settleRepairs() async throws(DaemonRequestError) -> RepairStatus {
        try await send("POST", "/api/repair/settle", body: nil)
    }

    public func startRepairs() async throws(DaemonRequestError) -> RepairStatus {
        try await send("POST", "/api/repair/now", body: nil)
    }

    public func stopRepairs() async throws(DaemonRequestError) -> RepairStatus {
        try await send("POST", "/api/repair/stop", body: nil)
    }

    public func updateRepairWorker(_ worker: RepairWorker) async throws(DaemonRequestError) -> RepairStatus {
        try await send("PUT", "/api/repair/worker", body: Self.json(worker))
    }
}
