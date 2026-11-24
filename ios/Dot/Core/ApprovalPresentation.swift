import Foundation

/// What an approval card says, in words an owner reads at a glance (item 1392). The daemon's summary of a step that asks
/// is one line for the engine: "Run: mv a b (mv takes files from outside the workspace) — aggressive preset still asks
/// for: destructive outside the workspace". The card takes it apart: a title that says what Dot wants to do, the command
/// alone in the mono block, and the reasons in a line of their own. Nothing is dropped: a summary this cannot take apart
/// is shown whole, as before.
struct ApprovalPresentation: Equatable, Sendable {
    /// What Dot wants to do: "Run a command".
    let title: String
    /// The part that is code or a path, for the mono block; the whole summary when it cannot be told apart.
    let command: String
    /// Why Dot asks, as the daemon said it (a note after the command and the policy's reason), nil when it said nothing.
    let why: String?

    /// The words the policy puts before the names it asks for in the aggressive preset (`policy/engine.py`); they say what
    /// the card's own "Why Dot asks" line says, so they are taken off its start and nowhere else.
    static let fixedReason: String = "aggressive preset still asks for:"

    static func make(tool: String, summary: String) -> ApprovalPresentation {
        let whole: String = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        var head: String = whole
        var reasons: [String] = []
        // The daemon puts its reason after the LAST " — " of the line. The command before it may hold a dash itself
        // (`echo "a — b" && rm -rf ~`): cut at the first one, the card would show only the beginning of the command and
        // the owner would allow what they did not read. The reason is the policy's own text and holds none. Whatever
        // follows the separator is shown, either as the reason or, with the policy's fixed words taken off its start, as
        // what is left of it.
        if let dash: Range<String.Index> = whole.range(of: " — ", options: .backwards) {
            head = String(whole[..<dash.lowerBound])
            let tail: String = String(whole[dash.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            if tail.hasPrefix(Self.fixedReason) {
                let reason: String = String(tail.dropFirst(Self.fixedReason.count)).trimmingCharacters(in: .whitespacesAndNewlines)
                if !reason.isEmpty { reasons.append(reason) }
            } else if !tail.isEmpty {
                reasons.append(tail)
            }
        }
        // A trailing " (…)" after the command is the daemon's note on it; a "(" glued to a word ($(date)) is the command's.
        if head.hasSuffix(")"), let open: Range<String.Index> = head.range(of: " (", options: .backwards) {
            let note: String = String(head[open.upperBound..<head.index(before: head.endIndex)])
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let before: String = String(head[..<open.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            if !note.isEmpty, !before.isEmpty, !note.contains("("), !note.contains(")") {
                reasons.insert(note, at: 0)
                head = before
            }
        }
        if (tool == "shell" || tool == "run_shell"), head.hasPrefix("Run: ") { head = String(head.dropFirst(5)) }
        let command: String = head.isEmpty ? whole : head
        return ApprovalPresentation(
            title: title(of: tool), command: command, why: reasons.isEmpty ? nil : reasons.joined(separator: "; "))
    }

    /// The tool as the step it asks for.
    static func title(of tool: String) -> String {
        switch tool {
        case "shell", "run_shell": return "Run a command"
        case "write_file": return "Write a file"
        case "edit_file": return "Change a file"
        case "write_notes": return "Write notes"
        case "apple_script": return "Run an AppleScript"
        case "web_fetch", "web_extract", "web_crawl": return "Open a web page"
        case "send_to_chat", "draft_message": return "Send a message"
        case "mail_read", "mail_search": return "Read mail"
        case "calendar_events": return "Read the calendar"
        case "schedule_create": return "Create a schedule"
        case "schedule_delete": return "Delete a schedule"
        case "delegate": return "Start a helper"
        case "ask_secret": return "Dot needs a secret"
        case "ask_user": return "Dot has a question"
        default:
            if tool.hasPrefix("browser_") { return "Use the browser" }
            if tool.hasPrefix("desktop_") { return "Use the Mac's screen" }
            if tool.hasPrefix("memory_") { return "Change what Dot remembers" }
            if tool.isEmpty { return "A step" }
            return tool.replacingOccurrences(of: "_", with: " ").capitalizedFirst
        }
    }
}
