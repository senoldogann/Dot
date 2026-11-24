import Foundation

/// The icon of a tool step, chosen from the tool's name, so a run's steps read at a glance as what they are. Dot's own tools
/// get the web app's icon for them (`toolIconName` in `static/js/icons.js`: a browser or a page the globe, a command the
/// terminal, a read an eye, a write a file, a search a magnifier, and so on); a name that is another harness's (`Bash`,
/// `WebSearch`, `run_shell`) is read by its words, and a name no rule knows gets the wrench.
enum ToolGlyph {
    static func icon(for tool: String) -> IconName {
        if tool.hasPrefix("browser_") || tool.hasPrefix("web_") { return .globe }
        if tool == "desktop_clipboard_read" || tool == "desktop_clipboard_write" { return .copy }
        if tool.hasPrefix("desktop_") || tool == "apple_script" { return .monitor }
        if tool == "shell" || tool.hasPrefix("terminal_") { return .terminal }
        if tool == "read_file" { return .eye }
        if tool == "find_files" || tool == "search_files" { return .search }
        if tool == "write_file" || tool == "edit_file" { return .file }
        if tool == "list_dir" { return .list }
        if tool.hasPrefix("memory_") { return .database }
        if tool.hasPrefix("schedule_") { return .calendar }
        if tool == "delegate" { return .branch }
        if tool == "update_plan" || tool == "mark_plan_step" { return .list }
        if tool == "ask_user" { return .help }
        if tool == "ask_secret" { return .key }
        if tool == "notify" { return .bell }
        if tool == "use_tools" { return .layers }
        if tool == "skill" { return .puzzle }
        if tool.hasPrefix("mcp__") { return .plug }
        return byWords(tool.lowercased())
    }

    /// What a name of another harness says by its words (`Bash`, `WebSearch`, `NotebookEdit`).
    private static func byWords(_ name: String) -> IconName {
        let rules: [(words: [String], icon: IconName)] = [
            (["delegate", "subagent", "helper", "agent"], .users),
            (["mail", "email", "message", "send"], .mail),
            (["shell", "bash", "command", "terminal", "exec"], .terminal),
            (["browser", "fetch", "page", "navigate", "click"], .globe),
            (["search", "find", "grep"], .search),
            (["write", "edit", "patch", "create_file", "save"], .squarePen),
            (["read", "file", "folder", "dir", "open"], .file),
            (["list"], .list),
            (["calendar", "event", "remind", "schedule", "loop", "cron", "timer", "wait"], .calendar),
            (["memory", "remember", "recall", "note"], .database),
            (["desktop", "screen", "mac", "app", "window"], .monitor),
            (["think", "plan", "todo"], .list),
            (["ask", "question"], .help),
        ]
        for rule in rules where rule.words.contains(where: { name.contains($0) }) { return rule.icon }
        return .wrench
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
