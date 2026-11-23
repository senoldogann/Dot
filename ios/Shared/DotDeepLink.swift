import Foundation

/// A link into the app that is not an address on the daemon: the Live Activity's rows and its lock-screen tap open the
/// chat of a run with `dotagent://run/<run id>`. Compiled into the app and the widget extension. Anything else, and a
/// run id that is not plainly an id, opens nothing: a URL scheme is a door any app on the phone may knock on.
enum DotDeepLink: Equatable, Sendable {
    case run(String)

    static let scheme: String = "dotagent"

    /// The link that opens the chat of `runId`, or `nil` for an id that is not one.
    static func url(forRun runId: String) -> URL? {
        guard isRunId(runId) else { return nil }
        return URL(string: "\(scheme)://run/\(runId)")
    }

    /// What `url` asks for, or `nil` when it asks for nothing this app does.
    static func parse(_ url: URL) -> DotDeepLink? {
        guard url.scheme?.lowercased() == scheme, url.host()?.lowercased() == "run" else { return nil }
        let parts: [Substring] = url.path().split(separator: "/", omittingEmptySubsequences: true)
        guard parts.count == 1, isRunId(String(parts[0])) else { return nil }
        return .run(String(parts[0]))
    }

    static func isRunId(_ text: String) -> Bool {
        !text.isEmpty && text.count <= 64 && text.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }
    }
}
