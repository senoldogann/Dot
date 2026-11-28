import Foundation

public enum BrowserChoice: String, Codable, CaseIterable, Sendable {
    case own, attach
}

public enum BrowserAppearance: String, Codable, CaseIterable, Sendable {
    case system, light, dark
}

public struct BrowserPageLook: Codable, Equatable, Sendable {
    public let fitWindow: Bool
    public let viewportWidth: Int
    public let viewportHeight: Int
    public let appearance: BrowserAppearance
    public let zoom: Int

    public init(fitWindow: Bool, viewportWidth: Int, viewportHeight: Int, appearance: BrowserAppearance, zoom: Int) {
        self.fitWindow = fitWindow
        self.viewportWidth = viewportWidth
        self.viewportHeight = viewportHeight
        self.appearance = appearance
        self.zoom = zoom
    }

    private enum CodingKeys: String, CodingKey {
        case appearance, zoom
        case fitWindow = "fit_window"
        case viewportWidth = "viewport_width"
        case viewportHeight = "viewport_height"
    }
}

public struct BrowserSettingsView: Decodable, Sendable {
    public let mode: BrowserChoice
    public let enabled: Bool
    public let connected: Bool
    public let signingIn: Bool
    public let raiseWindow: Bool
    public let remoteInput: Bool
    public let debugPort: Int
    public let profile: String
    public let profiles: [BrowserProfile]
    public let look: BrowserPageLook

    private enum CodingKeys: String, CodingKey {
        case mode, enabled, connected, profile, profiles, look
        case signingIn = "signing_in"
        case raiseWindow = "raise_window"
        case remoteInput = "remote_input"
        case debugPort = "debug_port"
    }
}

public struct BrowserProfile: Decodable, Identifiable, Sendable {
    public let name: String
    public let kind: Kind

    public enum Kind: String, Decodable, Sendable {
        case `default`, incognito, added
    }

    public var id: String { name }
}

public struct BrowserProfileCheck: Decodable, Sendable {
    public let readable: Int
}

private struct BrowserModeBody: Encodable { let mode: BrowserChoice }
private struct BrowserEnabledBody: Encodable { let enabled: Bool }
private struct BrowserRaiseBody: Encodable { let raise_window: Bool }
private struct BrowserInputBody: Encodable { let remote_input: Bool }
private struct BrowserPortBody: Encodable { let debug_port: Int }
private struct BrowserProfileBody: Encodable { let name: String }

extension DaemonAPI {
    public func browserSettings() async throws(DaemonRequestError) -> BrowserSettingsView {
        try await get("/api/browser", query: [])
    }

    public func setBrowserChoice(_ mode: BrowserChoice) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/mode", body: Self.json(BrowserModeBody(mode: mode)))
    }

    public func setBrowserEnabled(_ enabled: Bool) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/enabled", body: Self.json(BrowserEnabledBody(enabled: enabled)))
    }

    public func setBrowserRaise(_ raised: Bool) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/raise", body: Self.json(BrowserRaiseBody(raise_window: raised)))
    }

    public func setBrowserRemoteInput(_ enabled: Bool) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/remote-input", body: Self.json(BrowserInputBody(remote_input: enabled)))
    }

    public func setBrowserDebugPort(_ port: Int) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/debug-port", body: Self.json(BrowserPortBody(debug_port: port)))
    }

    public func setBrowserPageLook(_ look: BrowserPageLook) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/look", body: Self.json(look))
    }

    public func addBrowserProfile(_ name: String) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/profiles", body: Self.json(BrowserProfileBody(name: name)))
    }

    public func useBrowserProfile(_ name: String) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/profile", body: Self.json(BrowserProfileBody(name: name)))
    }

    public func openBrowserProfileForSignIn() async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/sign-in", body: nil)
    }

    public func clearBrowserProfile(_ name: String) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("POST", "/api/browser/profiles/\(name)/clear", body: nil)
    }

    public func checkBrowserProfile(_ name: String) async throws(DaemonRequestError) -> BrowserProfileCheck {
        try await send("POST", "/api/browser/profiles/\(name)/check", body: nil)
    }

    public func deleteBrowserProfile(_ name: String) async throws(DaemonRequestError) -> BrowserSettingsView {
        try await send("DELETE", "/api/browser/profiles/\(name)", body: nil)
    }
}
