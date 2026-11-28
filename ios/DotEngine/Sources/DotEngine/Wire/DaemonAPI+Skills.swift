import Foundation

public struct LearnedSkills: Decodable, Sendable {
    public let items: [LearnedSkill]
}

public struct LearnedSkill: Decodable, Identifiable, Sendable {
    public let name: String
    public let description: String
    public let pinned: Bool
    public let resting: Bool
    public let retired: Bool
    public let shared: Bool
    public let agentId: String?

    public var id: String { name }

    private enum CodingKeys: String, CodingKey {
        case name, description, pinned, resting, retired, shared
        case agentId = "agent_id"
    }
}

public struct SkillVersions: Decodable, Sendable {
    public let items: [LearnedSkillVersion]
}

public struct LearnedSkillVersion: Decodable, Identifiable, Sendable {
    public let note: Note
    public let current: Bool
    public let text: SkillText

    public var id: Int { note.n }

    public struct Note: Decodable, Sendable {
        public let n: Int
        public let at: Double
        public let tainted: Bool?
    }

    public struct SkillText: Decodable, Sendable {
        public let description: String
        public let when: String
        public let steps: [String]
    }
}

public struct InstalledPlugins: Decodable, Sendable {
    public let plugins: [GalleryPlugin]
    public let others: [OtherPlugin]
}

public struct GalleryPlugin: Decodable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let does: String
    public let state: State
    public let revertible: Bool

    public enum State: String, Decodable, Sendable {
        case available, installed, outdated, updated, taken
    }

    public var installed: Bool {
        switch state {
        case .installed, .outdated, .updated: true
        case .available, .taken: false
        }
    }
}

public struct OtherPlugin: Decodable, Identifiable, Sendable {
    public let name: String
    public let description: String?
    public let servers: Int
    public let own: Bool

    public var id: String { name }
}

private struct SkillFlag: Encodable { let pinned: Bool }
private struct SkillSharing: Encodable { let shared: Bool }
private struct SkillRestoration: Encodable { let version: Int }

extension DaemonAPI {
    public func learnedSkills() async throws(DaemonRequestError) -> LearnedSkills {
        try await get("/api/skills/learned", query: [])
    }

    public func learnedSkillVersions(_ name: String) async throws(DaemonRequestError) -> SkillVersions {
        try await get("/api/skills/learned/\(name)/versions", query: [])
    }

    public func pinLearnedSkill(_ name: String, pinned: Bool) async throws(DaemonRequestError) -> LearnedSkills {
        try await send("PUT", "/api/skills/learned/\(name)/pin", body: Self.json(SkillFlag(pinned: pinned)))
    }

    public func shareLearnedSkill(_ name: String, shared: Bool) async throws(DaemonRequestError) -> LearnedSkills {
        try await send("PUT", "/api/skills/learned/\(name)", body: Self.json(SkillSharing(shared: shared)))
    }

    public func restoreLearnedSkill(_ name: String, version: Int) async throws(DaemonRequestError) -> LearnedSkills {
        try await send("POST", "/api/skills/learned/\(name)/restore", body: Self.json(SkillRestoration(version: version)))
    }

    public func restLearnedSkill(_ name: String) async throws(DaemonRequestError) -> LearnedSkills {
        try await send("POST", "/api/skills/learned/\(name)/rest", body: nil)
    }

    public func wakeLearnedSkill(_ name: String) async throws(DaemonRequestError) -> LearnedSkills {
        try await send("POST", "/api/skills/learned/\(name)/wake", body: nil)
    }

    public func removeLearnedSkill(_ name: String) async throws(DaemonRequestError) -> LearnedSkills {
        try await send("DELETE", "/api/skills/learned/\(name)", body: nil)
    }

    public func installedPlugins() async throws(DaemonRequestError) -> InstalledPlugins {
        try await get("/api/plugin-gallery", query: [])
    }

    public func revertInstalledPlugin(_ id: String) async throws(DaemonRequestError) -> InstalledPlugins {
        try await send("POST", "/api/plugin-gallery/\(id)/revert", body: nil)
    }

    public func removeInstalledPlugin(_ id: String) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/installed-plugins/\(id)", body: nil)
    }
}
