import Foundation

/// The APNs environment follows the signed build's entitlement, regardless of its Debug or Release configuration.
public enum PhonePushEnvironment: String, Encodable, Sendable {
    case sandbox
    case production

    public init?(entitlement: String) {
        switch entitlement {
        case "development": self = .sandbox
        case "production": self = .production
        default: return nil
        }
    }
}

public struct PhonePushToken: Equatable, Sendable {
    fileprivate let hex: String

    public init?(_ bytes: Data) {
        guard (32...256).contains(bytes.count) else { return nil }
        hex = bytes.map { String(format: "%02x", $0) }.joined()
    }
}

extension DaemonAPI {
    public func registerDevicePush(_ token: PhonePushToken, environment: PhonePushEnvironment) async throws(DaemonRequestError) {
        let body: DevicePushBody = DevicePushBody(deviceToken: token.hex, environment: environment)
        try await sendVoid("POST", "/api/phone/apns", body: Self.json(body))
    }

    public func registerLiveStart(_ token: PhonePushToken) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/phone/live-start-token", body: Self.json(StartPushBody(token: token.hex)))
    }

    public func forgetLiveStart() async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/phone/live-start-token", body: nil)
    }

    public func registerFleetPush(_ token: PhonePushToken) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/phone/apns/activity", body: Self.json(FleetPushBody(token: token.hex)))
    }

    public func forgetFleetPush(_ token: PhonePushToken) async throws(DaemonRequestError) {
        try await sendVoid("DELETE", "/api/phone/apns/activity", query: [URLQueryItem(name: "token", value: token.hex)])
    }

    private func sendVoid(_ method: String, _ path: String, query: [URLQueryItem]) async throws(DaemonRequestError) {
        _ = try await data(request(method: method, path: path, query: query, body: nil), path: path)
    }
}

private struct DevicePushBody: Encodable, Sendable {
    let deviceToken: String
    let environment: PhonePushEnvironment

    enum CodingKeys: String, CodingKey {
        case deviceToken = "device_token"
        case startToken = "start_token"
        case environment
    }

    func encode(to encoder: any Encoder) throws {
        var body: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try body.encode(deviceToken, forKey: .deviceToken)
        try body.encodeNil(forKey: .startToken)
        try body.encode(environment, forKey: .environment)
    }
}

private struct StartPushBody: Encodable, Sendable {
    let token: String
}

private struct FleetPushBody: Encodable, Sendable {
    let run_id: String = "fleet"
    let token: String
}

/// Notification and lock-screen links point at a daemon run, which the phone resolves to its chat.
public enum PhoneRunLink {
    public static func run(inRoute route: String) -> String? {
        let prefix: String = "#/runs/"
        guard route.hasPrefix(prefix) else { return nil }
        return validated(String(route.dropFirst(prefix.count)))
    }

    public static func run(inURL url: URL) -> String? {
        guard url.scheme == "dotnext", url.host() == "run",
            url.query() == nil, url.fragment() == nil, url.user() == nil, url.password() == nil, url.port == nil,
            url.path().hasPrefix("/")
        else { return nil }
        return validated(String(url.path().dropFirst()))
    }

    public static func url(forRun run: String) -> URL? {
        guard validated(run) != nil else { return nil }
        return URL(string: "dotnext://run/\(run)")
    }

    private static func validated(_ run: String) -> String? {
        guard run.hasPrefix("run_"), run.count > 4, run.count <= 64,
            run.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 95 || $0 == 45 })
        else { return nil }
        return run
    }
}
