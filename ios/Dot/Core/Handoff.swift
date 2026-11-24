import Foundation

/// The card a chat shows when a run waits for a step only the owner can do in the browser (a login, a 2FA step; item 1444).
/// The daemon lists it as a `handoff` row of `GET /api/needs-you` under a fixed line, `Dot needs you on <host>` (or
/// `Dot needs your help` when the step names no page), and never what the step is, the page showed or was typed: so the
/// card has nothing but the host to say, and says only a host that is plainly one.
struct HandoffCard: Equatable, Sendable {
    /// The id of the approval that holds the run (the decision route's), the run and the chat.
    let approvalId: String
    let runId: String
    let threadId: String
    /// The host of the page the step is on, when the daemon's line names one and it is plainly a host.
    let host: String?

    /// The start of the daemon's fixed line when it names a page (`HANDOFF_TITLE` in `notify/needs_you.py`).
    static let linePrefix: String = "Dot needs you on "

    var headline: String {
        host.map { "Dot is waiting for you in the browser on \($0)" } ?? "Dot is waiting for you in the browser"
    }

    /// What is left for the owner to know: the run does not go on without them, and where they can do it from the phone.
    static let explanation: String =
        "Dot cannot go on until you have done this step. Open the run in Safari and use Take over to do it from your phone."

    static let buttonTitle: String = "Open in Safari"

    /// The card of a row of the list, or `nil` for a row that is no hand-off.
    static func make(from item: NeedsYouItem) -> HandoffCard? {
        guard item.kind == .handoff else { return nil }
        return HandoffCard(approvalId: item.id, runId: item.runId, threadId: item.threadId, host: host(inLine: item.title))
    }

    /// The hand-off a chat shows: the newest of its own (the daemon lists one for a chat), `nil` when it has none.
    static func card(forChat thread: String, in list: NeedsYouList) -> HandoffCard? {
        list.items
            .filter { $0.threadId == thread && $0.kind == .handoff }
            .max { $0.since < $1.since }
            .flatMap(make(from:))
    }

    /// The host in `Dot needs you on <host>`, or `nil`: the fixed line without a page, a line of another shape, and anything
    /// after the prefix that is not plainly a host (letters, digits, `-`, `.`, `_`, `:` for an address) is never put on a card.
    static func host(inLine line: String) -> String? {
        guard line.hasPrefix(linePrefix) else { return nil }
        let rest: String = String(line.dropFirst(linePrefix.count)).trimmingCharacters(in: .whitespaces)
        return isPlainHost(rest) ? rest : nil
    }

    static func isPlainHost(_ text: String) -> Bool {
        guard !text.isEmpty, text.count <= 253 else { return false }
        let allowed: CharacterSet = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._:")
        return text.unicodeScalars.allSatisfy { allowed.contains($0) } && text.contains { $0.isLetter || $0.isNumber }
    }

    /// The run's page in the web UI, on the daemon's own address the app already knows (`https://<mac>/#/runs/<id>`), to open in
    /// Safari; `nil` for a run id that is not plainly one.
    func webURL(on host: DaemonHost) -> URL? {
        guard DotDeepLink.isRunId(runId) else { return nil }
        return routeURL("#/runs/\(runId)", host: host)
    }
}
