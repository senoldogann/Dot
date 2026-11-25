import Foundation

// Settings › System › Maintenance (item 1790): the provider programs Dot starts on the Mac. Whether interrupted tasks
// continue after a restart is `StatusView.resume` (SettingsModels.swift), changed with `setResume`.

/// `GET /api/diagnostics/programs` (item 1794): the provider programs Dot starts on the Mac.
public struct ProgramsView: Codable, Sendable, Hashable {
    public let programs: [ProviderProgram]

    public init(programs: [ProviderProgram]) { self.programs = programs }
}

/// A provider's program: where it is (`nil`: not installed), the version it says, and how Dot fares on it
/// (`supported`, `graceful`, `broken`, `unknown`, `missing`) with the daemon's words why.
public struct ProviderProgram: Codable, Sendable, Hashable, Identifiable {
    public let name: String
    public let command: String
    public let path: String?
    public let version: String?
    public let support: String
    public let why: String

    public var id: String { name }

    public init(name: String, command: String, path: String?, version: String?, support: String, why: String) {
        self.name = name
        self.command = command
        self.path = path
        self.version = version
        self.support = support
        self.why = why
    }
}
