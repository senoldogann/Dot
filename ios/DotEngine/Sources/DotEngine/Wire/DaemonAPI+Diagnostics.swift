import Foundation

public struct DiagnosticProcesses: Decodable, Sendable {
    public let processes: [DiagnosticProcess]
}

public struct DiagnosticProcess: Decodable, Identifiable, Sendable {
    public let pid: Int
    public let name: String
    public let cpuPercent: Double
    public let memoryBytes: Int
    public let runningSeconds: Double

    public var id: Int { pid }

    private enum CodingKeys: String, CodingKey {
        case pid, name
        case cpuPercent = "cpu_percent"
        case memoryBytes = "memory_bytes"
        case runningSeconds = "running_s"
    }
}

public struct DiagnosticDisk: Decodable, Sendable {
    public let folders: [DiagnosticFolder]
}

public struct DiagnosticFolder: Decodable, Identifiable, Sendable {
    public let name: String
    public let paths: [String]
    public let bytes: Int

    public var id: String { name }
}

public struct DiagnosticLogs: Decodable, Sendable {
    public let files: [DiagnosticLogFile]
}

public struct DiagnosticLogFile: Decodable, Identifiable, Sendable {
    public let name: String
    public let bytes: Int
    public var id: String { name }
}

public struct DiagnosticLogTail: Decodable, Sendable {
    public let lines: [String]
}

public struct DiagnosticPauses: Decodable, Sendable {
    public let stalls: [DiagnosticPause]
    public let threshold: Double

    private enum CodingKeys: String, CodingKey {
        case stalls
        case threshold = "stall_s"
    }
}

public struct DiagnosticPause: Decodable, Sendable {
    public let beganAt: Double
    public let seconds: Double
    public let ongoing: Bool
    public let stack: String

    private enum CodingKeys: String, CodingKey {
        case seconds, ongoing, stack
        case beganAt = "began_at"
    }
}

extension DaemonAPI {
    public func diagnosticProcesses() async throws(DaemonRequestError) -> DiagnosticProcesses {
        try await get("/api/diagnostics/processes", query: [])
    }

    public func diagnosticDisk() async throws(DaemonRequestError) -> DiagnosticDisk {
        try await get("/api/diagnostics/disk", query: [])
    }

    public func diagnosticLogs() async throws(DaemonRequestError) -> DiagnosticLogs {
        try await get("/api/diagnostics/logs", query: [])
    }

    public func diagnosticLog(_ name: String, lines: Int) async throws(DaemonRequestError) -> DiagnosticLogTail {
        try await get("/api/diagnostics/logs/\(name)", query: [URLQueryItem(name: "lines", value: String(lines))])
    }

    public func diagnosticPauses() async throws(DaemonRequestError) -> DiagnosticPauses {
        try await get("/api/diagnostics/stalls", query: [])
    }
}
