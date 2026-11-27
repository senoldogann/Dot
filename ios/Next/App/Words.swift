import DotEngine
import Foundation

// The words of the chat (wave N2), in one place so the phone says each thing one way. Where the web has a word file
// (`dockwords.js`, `linewords.js`, `approvals.js`), these are its words.

/// The app's strings in the language of `locale` (its `.lproj`); the main bundle for a language the app has none of,
/// which is the device's own choice of language.
func wordsBundle(_ locale: Locale) -> Bundle {
    guard let code: String = locale.language.languageCode?.identifier,
        let path: String = Bundle.main.path(forResource: code, ofType: "lproj"), let bundle: Bundle = Bundle(path: path)
    else { return .main }
    return bundle
}

/// The app's one duration shape (§19): "41 dk 15 sn", "1 sa 47 dk 23 sn", "14 sn"; English "41m 15s".
func durationWords(_ seconds: Int, locale: Locale) -> String {
    let parts: DurationParts = DurationParts.of(seconds)
    let bundle: Bundle = wordsBundle(locale)
    if parts.hours > 0 {
        if parts.showsSeconds {
            return String(
                localized: "\(parts.hours)h \(parts.minutes)m \(parts.seconds)s", bundle: bundle, locale: locale,
                comment: "A duration of hours, minutes and seconds, e.g. 1h 47m 23s")
        }
        return String(localized: "\(parts.hours)h \(parts.minutes)m", bundle: bundle, locale: locale, comment: "A duration of hours and minutes, e.g. 12h 5m")
    }
    if parts.minutes > 0 {
        return String(localized: "\(parts.minutes)m \(parts.seconds)s", bundle: bundle, locale: locale, comment: "A duration of minutes and seconds, e.g. 41m 15s")
    }
    return String(localized: "\(parts.seconds)s", bundle: bundle, locale: locale, comment: "A duration of seconds, e.g. 14s")
}

/// The fold's line: "Worked for 2m 14s", no counts (item 1904: the owner reads how long, the steps are a tap away).
func foldWords(_ fold: WorkFold, locale: Locale) -> String {
    let took: String = durationWords(fold.seconds, locale: locale)
    return String(localized: "Worked for \(took)", bundle: wordsBundle(locale), locale: locale, comment: "Work fold: how long a stretch of work took; the argument is a duration")
}

/// A tool call's line: the plain verb of its kind and its target ("Read /src/app.py", "Ran git status"); one of Dot's
/// own tools by its phrase ("Search notes · deploy"), any other by its name in words, never the raw name (item 1769).
func toolLine(_ entry: ToolEntry) -> String {
    let target: String = entry.target
    if entry.kind != .helper, entry.kind != .wait, let phrase = dotToolWords(entry.tool) {
        return target.isEmpty ? phrase : "\(phrase) · \(target)"
    }
    switch entry.kind {
    case .read where !target.isEmpty: return String(localized: "Read \(target)", comment: "Tool row: a file read; the argument is its path")
    case .edit where !target.isEmpty: return String(localized: "Edited \(target)", comment: "Tool row: a file edited; the argument is its path")
    case .command where !target.isEmpty:
        return entry.state == .running
            ? String(localized: "Running \(target)", comment: "Tool row: a command at work; the argument is the command")
            : String(localized: "Ran \(target)", comment: "Tool row: a command that ran; the argument is the command")
    case .search where !target.isEmpty: return String(localized: "Searched \(target)", comment: "Tool row: a search; the argument is what was searched")
    case .web where !target.isEmpty: return String(localized: "Browsed \(target)", comment: "Tool row: a browser or web step; the argument is its page")
    case .wait:
        return entry.state == .running
            ? String(localized: "Waiting for a condition", comment: "Tool row: a wait_until step at work")
            : String(localized: "Waited for a condition", comment: "Tool row: a wait_until step")
    case .helper where !target.isEmpty:
        switch entry.state {
        case .running: return String(localized: "Delegating “\(target)”", comment: "Tool row: a task handed to a helper, at work; the argument is its goal")
        case .done: return String(localized: "Delegated task “\(target)” finished", comment: "Tool row: a task handed to a helper, ended; the argument is its goal")
        case .failed: return String(localized: "Delegated task “\(target)” failed", comment: "Tool row: a task handed to a helper that failed; the argument is its goal")
        }
    default: return target.isEmpty ? toolName(entry.tool) : "\(toolName(entry.tool)) · \(target)"
    }
}

/// What a step does now, in plain words, for the working capsule and a helper's row (item 1880): never a raw tool name.
/// A step that talks to helpers waits for them; a read, edit, command, search, page or wait says what it does; any
/// other tool is "Working".
func stepWords(tool: String, target: String) -> String {
    let kind: ToolKind = ToolKind.of(tool)
    if isHelperStep(tool) {
        return String(localized: "Waiting for a helper", comment: "Capsule and helper row: the run's step talks to or waits for a helper")
    }
    switch kind {
    case .command:
        // A command's own words are no line for a row (owner 2026-11-27: no raw paths in rows).
        return String(localized: "Running a command", comment: "Work line: a command")
    case .read, .edit, .search, .web, .wait:
        // A file by its name, never its whole path.
        let shown: String = kind == .read || kind == .edit ? fileName(of: target) : target
        let line: String = toolLine(
            ToolEntry(id: "", tool: tool, kind: kind, target: shown, args: "", output: "", state: .running, images: [], startedAt: 0, endedAt: nil))
        return line == tool || line.hasPrefix(tool + " · ") ? String(localized: "Working", comment: "Status word: a run is at work") : line
    default:
        return String(localized: "Working", comment: "Status word: a run is at work")
    }
}

/// The last part of a path ("…/memory-merge/C8.md" → "C8.md"); a text with no slash as it is.
func fileName(of path: String) -> String {
    let trimmed: String = path.trimmingCharacters(in: CharacterSet(charactersIn: "/ `\"'"))
    return trimmed.split(separator: "/").last.map(String.init) ?? trimmed
}

/// Whether a step starts or talks to a helper: Dot's `delegate` and `message_agent`, a CLI harness's `Task` or `Agent`.
/// Named exactly: a tool that only has "agent" in its name (`agent_status`, an MCP tool) is no wait on a helper.
func isHelperStep(_ tool: String) -> Bool {
    helperWaitTools.contains(tool.lowercased())
}

/// The glyph of a tool call by its kind.
func toolSymbol(_ kind: ToolKind) -> String {
    switch kind {
    case .command: "apple.terminal"
    case .read: "eye"
    case .edit: "pencil"
    case .search: "magnifyingglass"
    case .wait: "hourglass"
    case .web: "globe"
    case .screen: "cursorarrow.click"
    case .helper: "hammer"
    case .other: "hammer"
    }
}

/// What a reaction means (docs/runs.md), the words its menu and VoiceOver say.
enum ReactionKind: String, CaseIterable, Identifiable {
    case good
    case wrong
    case hold
    case remember
    case again
    case explain

    var id: String { rawValue }

    var emoji: String {
        switch self {
        case .good: "👍"
        case .wrong: "👎"
        case .hold: "✋"
        case .remember: "📌"
        case .again: "🔁"
        case .explain: "❓"
        }
    }

    var meaning: String {
        switch self {
        case .good: String(localized: "Good: Dot keeps doing it this way", comment: "Reaction menu: what 👍 does")
        case .wrong: String(localized: "Wrong: reaches a working run at its next step", comment: "Reaction menu: what 👎 does")
        case .hold: String(localized: "Hold: the run waits until you write", comment: "Reaction menu: what ✋ does")
        case .remember: String(localized: "Pin: Dot keeps a note of it", comment: "Reaction menu: what 📌 does")
        case .again: String(localized: "Again: Dot does it once more", comment: "Reaction menu: what 🔁 does")
        case .explain: String(localized: "Explain: Dot says why", comment: "Reaction menu: what ❓ does")
        }
    }
}

/// The ask's title, by its kind (`approvals.js` `titleOf`).
func askTitle(_ ask: Ask) -> String {
    if ask.kept { return String(localized: "Kept for you", comment: "Ask dock title: a step kept after nobody answered in time") }
    switch ask.kind {
    case .question: return String(localized: "Dot is asking you", comment: "Ask dock title: a question")
    case .secret: return String(localized: "Dot asks for a secret", comment: "Ask dock title: a key, token or password")
    case .handoff: return String(localized: "Dot needs you to take over this step", comment: "Ask dock title: a hand-off")
    case .approval: return String(localized: "Approval needed", comment: "Ask dock title: an approval")
    }
}

/// How an ask stands, in words; `nil` while it waits.
func askPhaseWords(_ phase: AskPhase) -> String? {
    switch phase {
    case .pending: nil
    case .submitting: String(localized: "Sending your answer…", comment: "Ask dock: the answer is on its way")
    case .queuedOffline: String(localized: "Your answer goes once the phone is online", comment: "Ask dock: the answer is kept while offline")
    case .answeredElsewhere: String(localized: "Answered on your Mac or another device", comment: "Ask dock: someone answered it elsewhere")
    case .expired: String(localized: "Nobody answered in time", comment: "Ask dock: the ask expired")
    case .refused(let detail):
        detail.isEmpty
            ? String(localized: "The Mac refused the answer", comment: "Ask dock: the answer was refused without a reason")
            : String(localized: "The Mac refused the answer: \(detail)", comment: "Ask dock: the answer was refused; the argument is the Mac's words")
    case .retry: String(localized: "The answer could not be sent", comment: "Ask dock: sending failed; Try again follows")
    }
}

/// An ask left in the transcript: what was decided and who decided.
func anchorWords(_ anchor: AskAnchor) -> String {
    let what: String =
        switch (anchor.status, anchor.severity) {
        case ("pending", _): String(localized: "Waiting for your answer", comment: "Transcript anchor: an ask the dock holds")
        case ("left", _): String(localized: "Dot asked and went on", comment: "Transcript anchor: a question the run left")
        case ("expired", _): String(localized: "Nobody answered in time", comment: "Ask dock: the ask expired")
        case ("approved", "question"): String(localized: "Answered", comment: "Transcript anchor: a question answered")
        case ("approved", "handoff"): String(localized: "Done by you", comment: "Transcript anchor: a hand-off done")
        case ("approved", "secret"): String(localized: "Saved securely", comment: "Transcript anchor: a secret given (§20: stays one line)")
        case ("approved", _): String(localized: "Allowed", comment: "Transcript anchor: an approval allowed")
        case ("denied", "question"), ("denied", "handoff"): String(localized: "Skipped", comment: "Transcript anchor: a question or hand-off skipped")
        case ("denied", "secret"): String(localized: "Declined", comment: "Transcript anchor: a secret declined")
        case ("denied", _): String(localized: "Denied", comment: "Transcript anchor: an approval denied")
        default: anchor.status
        }
    let who: String? =
        switch anchor.decider {
        case .thisPhone: String(localized: "by you on this phone", comment: "Transcript anchor: who answered")
        case .elsewhere: String(localized: "by you on the Mac or the web", comment: "Transcript anchor: who answered")
        case .automatic(let name):
            name.isEmpty
                ? String(localized: "without asking", comment: "Transcript anchor: answered by the review model or a rule")
                : String(localized: "by \(name)", comment: "Transcript anchor: who answered; the argument is the review model's or rule's name")
        case .nobody, nil: nil
        }
    let answer: String = anchor.severity == "question" && anchor.status == "approved" && !anchor.note.isEmpty ? " → " + anchor.note : ""
    return [what, who].compactMap { $0 }.joined(separator: " ") + (anchor.summary.isEmpty ? "" : ": " + anchor.summary) + answer
}

/// What a press of the composer's button says.
func composerActionWords(_ action: ComposerAction) -> String {
    switch action {
    case .send: String(localized: "Send", comment: "VoiceOver: the composer's send button")
    case .queue: String(localized: "Queue: starts after this run", comment: "Composer: queues the message after the run at work")
    case .steer: String(localized: "Send now into the running task", comment: "Composer long-press: steer instead of queue")
    case .stop: String(localized: "Stop", comment: "Composer and ask dock: stops the run at work")
    case .confirmEdit: String(localized: "Save the queued message", comment: "Composer: confirms the edit of a queued message")
    }
}

/// The title of a way to send a follow-up while a run works, in the composer's long-press menu.
func followUpTitle(_ way: FollowUp) -> String {
    switch way {
    case .steer: String(localized: "Steer now", comment: "Composer long-press menu: sends the draft into the run at work")
    case .queue: String(localized: "Queue", comment: "Follow-up setting: queued after the run")
    }
}

/// What a way to send a follow-up does, under its title in the composer's long-press menu.
func followUpDetail(_ way: FollowUp) -> String {
    switch way {
    case .steer: String(localized: "Interrupt what the agent is doing", comment: "Composer long-press menu: what Steer now does")
    case .queue: String(localized: "Run after the current turn", comment: "Composer long-press menu: what Queue does")
    }
}

/// A picker section's header (PLAN §22).
func pickerHeader(_ kind: PickerKind) -> String {
    switch kind {
    case .command: String(localized: "Commands", comment: "Composer picker header: Dot's commands")
    case .skill: String(localized: "Skills", comment: "Composer picker header: the skills a run can use")
    case .agent: String(localized: "Agents", comment: "Chat header quick panel: the chat's helpers")
    case .team: String(localized: "Teams", comment: "Composer picker header: the owner's teams")
    case .chat: String(localized: "Chats", comment: "Title of the chat list")
    case .account: String(localized: "Accounts", comment: "Composer picker header: the accounts signed in")
    case .file: String(localized: "Files of this chat", comment: "Composer picker header: the files this chat's runs changed")
    }
}

func pickerSymbol(_ kind: PickerKind) -> String {
    switch kind {
    case .command: "apple.terminal"
    case .skill: "sparkles"
    case .agent: "person"
    case .team: "person.3"
    case .chat: "bubble.left"
    case .account: "envelope"
    case .file: "doc.text"
    }
}

/// Dot's own commands in the `/` panel (PLAN §22), each with what it does.
func composerCommands() -> [PickerItem] {
    [
        PickerItem(kind: .command, token: "/btw", name: "/btw", detail: String(localized: "Ask a side question the run does not see", comment: "Composer command: what /btw does")),
        PickerItem(kind: .command, token: "/side", name: "/side", detail: String(localized: "Same as /btw", comment: "Composer command: what /side does")),
        PickerItem(kind: .command, token: "/loop", name: "/loop", detail: String(localized: "Ask again every so often: /loop 30m <message>", comment: "Composer command: what /loop does")),
        PickerItem(kind: .command, token: "/plan", name: "/plan", detail: String(localized: "Make a plan first, then do it", comment: "Composer command: what /plan does")),
        PickerItem(kind: .command, token: "/model", name: "/model", detail: String(localized: "Switch the model of this chat", comment: "Composer command: what /model does")),
        PickerItem(kind: .command, token: "/stop", name: "/stop", detail: String(localized: "Stop the run at work", comment: "Composer command: what /stop does")),
    ]
}

/// A stretch of tool calls that ended well, said as one line in plain words (item 1909, after another coding agent's work
/// log): "Ran 3 commands and read 2 files", "Used 1 tool and searched the web 1 time". The kinds in the order they
/// first came, each with its count. Pure.
func toolGroupWords(_ entries: [ToolEntry], locale: Locale) -> String {
    var order: [String] = []
    var counts: [String: Int] = [:]
    for entry in entries {
        let kind: String = groupKind(entry)
        if counts[kind] == nil { order.append(kind) }
        counts[kind, default: 0] += 1
    }
    let bundle: Bundle = wordsBundle(locale)
    let parts: [String] = order.map { kindPhrase($0, counts[$0] ?? 0, bundle: bundle, locale: locale) }
    let sentence: String
    if parts.count <= 1 {
        sentence = parts.first ?? ""
    } else {
        let head: String = parts.dropLast().joined(separator: ", ")
        sentence = String(localized: "\(head) and \(parts[parts.count - 1])", bundle: bundle, locale: locale, comment: "Work log: the last part of a group of tool calls joined to the others; the arguments are the parts")
    }
    return sentence.prefix(1).uppercased(with: locale) + sentence.dropFirst()
}

/// The kind a tool call counts under in a group's line.
private func groupKind(_ entry: ToolEntry) -> String {
    if entry.tool == "web_search" { return "web_search" }
    switch entry.kind {
    case .command: return "command"
    case .read: return "read"
    case .edit: return "edit"
    case .search: return "search"
    case .web: return "web"
    case .screen: return "screen"
    case .wait: return "wait"
    case .helper: return "helper"
    case .other: return "other"
    }
}

private func kindPhrase(_ kind: String, _ count: Int, bundle: Bundle, locale: Locale) -> String {
    switch kind {
    case "command": String(localized: "ran \(count) commands", bundle: bundle, locale: locale, comment: "Work log group: commands run; the argument is how many")
    case "read": String(localized: "read \(count) files", bundle: bundle, locale: locale, comment: "Work log group: files read; the argument is how many")
    case "edit": String(localized: "edited \(count) files", bundle: bundle, locale: locale, comment: "Work log group: files edited; the argument is how many")
    case "web_search": String(localized: "searched the web \(count) times", bundle: bundle, locale: locale, comment: "Work log group: web searches; the argument is how many")
    case "search": String(localized: "searched \(count) times", bundle: bundle, locale: locale, comment: "Work log group: searches; the argument is how many")
    case "web": String(localized: "opened \(count) pages", bundle: bundle, locale: locale, comment: "Work log group: pages opened; the argument is how many")
    case "screen": String(localized: "took \(count) steps on the Mac", bundle: bundle, locale: locale, comment: "Work log group: steps on the Mac's screen; the argument is how many")
    case "wait": String(localized: "waited \(count) times", bundle: bundle, locale: locale, comment: "Work log group: waits; the argument is how many")
    case "helper": String(localized: "started \(count) helpers", bundle: bundle, locale: locale, comment: "Work log group: helpers started; the argument is how many")
    default: String(localized: "used \(count) tools", bundle: bundle, locale: locale, comment: "Work log group: other tools; the argument is how many")
    }
}
