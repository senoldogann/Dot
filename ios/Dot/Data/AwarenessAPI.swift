import Foundation

/// The calls the phone makes so that Dot knows when the owner is looking and the app knows when Dot needs them
/// (`docs/phone.md`, "The iPhone app's calls"): the activity lease, the push switches, what waits on the owner and what Dot
/// did while they were away. Each is the phone's own key on the daemon's own routes.
extension APIClient {
    /// A session for the lease's reports, made like `session` (the same protocols and proxies): one try, no waiting for a
    /// link and a short wait for the answer. A report that waits for a link would reach the daemon late, and the lease says
    /// where the app is now: one that does not get through is let go, and the daemon's lease ends by itself, which is the
    /// safe side (the alerts go).
    static func makeLeaseSession(like session: URLSession) -> URLSession {
        let configuration: URLSessionConfiguration = session.configuration
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 12
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }

    /// Says the app is in front of the owner and which chat it shows, or that it left (`POST /api/phone/activity`, item
    /// 1420). The reply (the lease as the daemon holds it) is not read. Use a client made with `makeLeaseSession`.
    func report(activity: ActivityReport) async throws {
        let path: String = "/api/phone/activity"
        var built: URLRequest = request("POST", path, body: try JSONEncoder().encode(activity))
        // The request's own wait, not a write's 30 s: a renewal that is not answered in 8 s is let go.
        built.timeoutInterval = 8
        _ = try await data(built, path: path)
    }

    // MARK: Push switches (item 1441)

    /// Which of the three kinds of push Dot tells this phone (`GET /api/phone/push-settings`).
    func pushSettings() async throws -> PushSettings { try await get("/api/phone/push-settings") }

    /// Keeps all three switches (`PUT /api/phone/push-settings`: the daemon takes the three together) and answers with what it
    /// kept. A write is sent once; the screen that flipped the switch says it when it does not get through.
    func setPushSettings(_ settings: PushSettings) async throws -> PushSettings {
        try await send("PUT", "/api/phone/push-settings", body: settings)
    }

    // MARK: What waits on the owner (item 1442)

    /// Everything that waits on the owner, newest first, with how many things that is (`GET /api/needs-you`): the approvals a
    /// run asks, its questions, its requests for a secret and its hand-offs. Any token reads it, a phone's key too.
    func needsYou() async throws -> NeedsYouList { try await get("/api/needs-you") }
}
