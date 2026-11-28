import Foundation

public struct SystemCheck: Decodable, Sendable {
    public let name: String
    public let ok: Bool
    public let required: Bool
    public let detail: String
}

public struct RestartAvailability: Decodable, Sendable {
    public let service: Bool
    public let serviceProblem: String?

    private enum CodingKeys: String, CodingKey {
        case service
        case serviceProblem = "service_problem"
    }
}

public struct RestartReceipt: Decodable, Sendable {
    public let restarting: Bool
}

extension DaemonAPI {
    public func systemChecks() async throws(DaemonRequestError) -> [SystemCheck] {
        try await get("/api/doctor", query: [])
    }

    public func restartAvailability() async throws(DaemonRequestError) -> RestartAvailability {
        try await get("/api/desktop/permissions", query: [])
    }

    public func restartDot() async throws(DaemonRequestError) -> RestartReceipt {
        try await send("POST", "/api/system/restart", body: nil)
    }
}
