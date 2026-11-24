import Foundation

/// The SF Symbol of a tool step, chosen from the tool's name (`run_shell`, `read_file`, `web_search`, `delegate`…), so a
/// run's steps read at a glance as what they are. A name no rule knows gets the wrench.
enum ToolGlyph {
    static func symbol(for tool: String) -> String {
        let name: String = tool.lowercased()
        let rules: [(words: [String], symbol: String)] = [
            (["delegate", "subagent", "helper", "agent"], "person.2"),
            (["shell", "bash", "command", "terminal", "exec"], "terminal"),
            (["browser", "web_fetch", "fetch", "page", "navigate", "click"], "globe"),
            (["search", "find", "grep"], "magnifyingglass"),
            (["write", "edit", "patch", "create_file", "save"], "square.and.pencil"),
            (["read", "list", "file", "folder", "dir", "open"], "doc.text"),
            (["mail", "email", "message", "send"], "envelope"),
            (["calendar", "event", "remind"], "calendar"),
            (["schedule", "loop", "cron", "timer", "wait"], "clock"),
            (["memory", "remember", "recall", "note"], "books.vertical"),
            (["desktop", "screen", "mac", "app", "window"], "macwindow"),
            (["think", "plan", "todo"], "list.bullet.clipboard"),
            (["ask", "question"], "questionmark.bubble"),
        ]
        for rule in rules where rule.words.contains(where: { name.contains($0) }) { return rule.symbol }
        return "wrench.and.screwdriver"
    }

    /// A step's line split at its first colon: the verb ("Shell") and what it did ("ls -l ~/Documents"). A line with no
    /// colon is all words.
    static func split(_ line: String) -> (verb: String, rest: String) {
        guard let colon: String.Index = line.firstIndex(of: ":") else { return ("", line) }
        let verb: String = String(line[..<colon])
        let rest: String = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        // A colon deep in a sentence is not a verb's: only a short first word or two is.
        return verb.count <= 24 ? (verb, rest) : ("", line)
    }
}
