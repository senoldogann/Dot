import DotEngine
import Foundation

// A tool call's name in words (item 1769): Dot's own tools with the web's phrases (`tools.js` TOOL_WORDS, the same
// English and Turkish), any other name as a sentence with its first letter capital, never the raw `work_orders`.

/// The phrase of one of Dot's own tools (delegation, notes, schedules, asks, pages, the run's end), or `nil` for a tool
/// of the Mac, the web or a connected app. A row of such a tool wears Dot's mark instead of a symbol.
func dotToolWords(_ tool: String) -> String? {
    switch tool {
    case "finish": String(localized: "Finish", comment: "Tool row of Dot's own tool: the run gives its answer")
    case "use_tools": String(localized: "Turn on more actions", comment: "Tool row of Dot's own tool: the run asks for more of its tools")
    case "work_orders": String(localized: "Work orders", comment: "Tool row of Dot's own tool: the agents' work orders")
    case "delegate": String(localized: "Delegate", comment: "Tool row of Dot's own tool: a part handed to a helper")
    case "message_agent": String(localized: "Message agent", comment: "Tool row of Dot's own tool: a message to another agent")
    case "consult": String(localized: "Ask a helper", comment: "Tool row of Dot's own tool: a question to a helper")
    case "create_agent": String(localized: "Make agent", comment: "Tool row of Dot's own tool: a new agent")
    case "create_team": String(localized: "Make team", comment: "Tool row of Dot's own tool: a new team")
    case "agents_now": String(localized: "Agents at work", comment: "Tool row of Dot's own tool: what the agents do now")
    case "notify": String(localized: "Notify", comment: "Tool row of Dot's own tool: a notification to the owner")
    case "show_page": String(localized: "Show page", comment: "Tool row of Dot's own tool: a page shown in the chat")
    case "show_cards": String(localized: "Show cards", comment: "Tool row of Dot's own tool: cards shown in the chat")
    case "draft_message": String(localized: "Draft message", comment: "Tool row of Dot's own tool: a message drafted for the owner")
    case "send_to_chat": String(localized: "Write to a chat", comment: "Tool row of Dot's own tool: words sent to another chat")
    case "read_chat", "read_agent_chat": String(localized: "Read a chat", comment: "Tool row of Dot's own tool: another chat read")
    case "search_chats", "chats": String(localized: "Search the chats", comment: "Tool row of Dot's own tool: the chats searched")
    case "ask_user": String(localized: "Ask you", comment: "Tool row of Dot's own tool: a question to the owner")
    case "ask_secret": String(localized: "Ask you for a secret", comment: "Tool row of Dot's own tool: a secret asked of the owner")
    case "memory_save": String(localized: "Save note", comment: "Tool row of Dot's own tool: a note saved")
    case "memory_search", "recall": String(localized: "Search notes", comment: "Tool row of Dot's own tool: the notes searched")
    case "memory_update": String(localized: "Update note", comment: "Tool row of Dot's own tool: a note changed")
    case "memory_forget": String(localized: "Forget note", comment: "Tool row of Dot's own tool: a note forgotten")
    case "write_notes": String(localized: "Write notes", comment: "Tool row of Dot's own tool: the run's notes written")
    case "skill": String(localized: "Use skill", comment: "Tool row of Dot's own tool: a skill used")
    case "find_help": String(localized: "Find help", comment: "Tool row of Dot's own tool: help looked up")
    case "schedule_create": String(localized: "Add schedule", comment: "Tool row of Dot's own tool: a schedule added")
    case "schedule_list": String(localized: "List schedules", comment: "Tool row of Dot's own tool: the schedules listed")
    case "schedule_delete": String(localized: "Delete schedule", comment: "Tool row of Dot's own tool: a schedule deleted")
    case "update_plan": String(localized: "Update plan", comment: "Tool row of Dot's own tool: the run's plan changed")
    case "mark_plan_step": String(localized: "Mark plan step", comment: "Tool row of Dot's own tool: a step of the plan marked")
    case "todo_add": String(localized: "Add to-do", comment: "Tool row of Dot's own tool: a to-do added")
    case "todo_drop": String(localized: "Drop to-do", comment: "Tool row of Dot's own tool: a to-do dropped")
    case "react": String(localized: "React to your message", comment: "Tool row of Dot's own tool: a reaction on the owner's message")
    case "wait_until": String(localized: "Wait", comment: "Tool row of Dot's own tool: a wait until a condition holds")
    default: tool.hasPrefix("campaign_") || tool.hasPrefix("objective") ? toolName(tool) : nil
    }
}

/// Whether a call is one of Dot's own tools, which wears Dot's mark; a task handed to a helper wears the hammer, as the
/// owner's reference draws it.
func isDotTool(_ entry: ToolEntry) -> Bool {
    entry.kind != .helper && dotToolWords(entry.tool) != nil
}

/// A tool's name in words as the web says it: `draft_reply` reads "Draft reply", a connected app's
/// `mcp__microsoft-learn__search` reads "Microsoft Learn · Search".
func toolName(_ tool: String) -> String {
    // The Dot Next app's own asks (item 2051), in the web's words (`tools.js`).
    switch tool {
    case "phone_calendar_events": return String(localized: "iPhone calendar", comment: "Tool row: the iPhone's calendars read through Dot Next")
    case "phone_calendar_add": return String(localized: "Add to iPhone calendar", comment: "Tool row: an event added to the iPhone's calendar through Dot Next")
    case "phone_reminder_add": return String(localized: "Add iPhone reminder", comment: "Tool row: a reminder added on the iPhone through Dot Next")
    case "phone_location": return String(localized: "iPhone location", comment: "Tool row: the iPhone's location asked through Dot Next")
    default: break
    }
    let parts: [Substring] = tool.split(separator: "__", omittingEmptySubsequences: false).map { $0 }
    if parts.count >= 3, parts[0] == "mcp" {
        let server: String = String(parts[1])
        let name: String = parts[2...].joined(separator: "__")
        let own: String = server.replacingOccurrences(of: "-", with: "_").lowercased() + "_"
        let rest: String = name.lowercased().hasPrefix(own) && name.count > own.count ? String(name.dropFirst(own.count)) : name
        return titled(server) + " · " + sentence(rest)
    }
    return sentence(tool)
}

/// Words with their first letter capital: `draft_reply` reads "Draft reply".
func sentence(_ name: String) -> String {
    let words: String = name.replacingOccurrences(of: "_", with: " ").replacingOccurrences(of: "-", with: " ").trimmingCharacters(in: .whitespaces)
    guard let first = words.first else { return name }
    return first.uppercased() + words.dropFirst()
}

/// A server's name as a title: `microsoft-learn` reads "Microsoft Learn".
private func titled(_ name: String) -> String {
    name.split(whereSeparator: { $0 == "-" || $0 == "_" }).map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
}
