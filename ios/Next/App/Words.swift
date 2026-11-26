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

/// "read 3 files, ran 2 commands" for a fold's tally.
func tallyWords(_ tally: WorkTally, locale: Locale) -> String {
    tally.ordered.map { kind, count in kindWords(kind, count, locale: locale) }.joined(separator: ", ")
}

private func kindWords(_ kind: ToolKind, _ count: Int, locale: Locale) -> String {
    let bundle: Bundle = wordsBundle(locale)
    let one: Bool = count == 1
    switch kind {
    case .read:
        return one
            ? String(localized: "read 1 file", bundle: bundle, locale: locale, comment: "Work fold tally: one file read")
            : String(localized: "read \(count) files", bundle: bundle, locale: locale, comment: "Work fold tally: files read; the argument is how many")
    case .command:
        return one
            ? String(localized: "ran 1 command", bundle: bundle, locale: locale, comment: "Work fold tally: one command run")
            : String(localized: "ran \(count) commands", bundle: bundle, locale: locale, comment: "Work fold tally: commands run; the argument is how many")
    case .edit:
        return one
            ? String(localized: "edited 1 file", bundle: bundle, locale: locale, comment: "Work fold tally: one file edited")
            : String(localized: "edited \(count) files", bundle: bundle, locale: locale, comment: "Work fold tally: files edited; the argument is how many")
    case .search:
        return one
            ? String(localized: "searched once", bundle: bundle, locale: locale, comment: "Work fold tally: one search")
            : String(localized: "searched \(count) times", bundle: bundle, locale: locale, comment: "Work fold tally: searches; the argument is how many")
    case .web:
        return one
            ? String(localized: "1 web step", bundle: bundle, locale: locale, comment: "Work fold tally: one browser or web step")
            : String(localized: "\(count) web steps", bundle: bundle, locale: locale, comment: "Work fold tally: browser or web steps; the argument is how many")
    case .screen:
        return one
            ? String(localized: "1 screen step", bundle: bundle, locale: locale, comment: "Work fold tally: one step on the Mac's screen")
            : String(localized: "\(count) screen steps", bundle: bundle, locale: locale, comment: "Work fold tally: steps on the Mac's screen; the argument is how many")
    case .wait:
        return one
            ? String(localized: "waited once", bundle: bundle, locale: locale, comment: "Work fold tally: one wait")
            : String(localized: "waited \(count) times", bundle: bundle, locale: locale, comment: "Work fold tally: waits; the argument is how many")
    case .helper:
        return one
            ? String(localized: "started 1 helper", bundle: bundle, locale: locale, comment: "Work fold tally: one helper started")
            : String(localized: "started \(count) helpers", bundle: bundle, locale: locale, comment: "Work fold tally: helpers started; the argument is how many")
    case .other:
        return one
            ? String(localized: "1 other step", bundle: bundle, locale: locale, comment: "Work fold tally: one step of another kind")
            : String(localized: "\(count) other steps", bundle: bundle, locale: locale, comment: "Work fold tally: steps of other kinds; the argument is how many")
    }
}

/// The fold's line: "Worked for 2m 14s · read 3 files, ran 2 commands".
func foldWords(_ fold: WorkFold, locale: Locale) -> String {
    let took: String = durationWords(fold.seconds, locale: locale)
    let tally: String = tallyWords(fold.tally, locale: locale)
    let worked: String = String(localized: "Worked for \(took)", bundle: wordsBundle(locale), locale: locale, comment: "Work fold: how long a stretch of work took; the argument is a duration")
    return tally.isEmpty ? worked : worked + " · " + tally
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
    case .stop: String(localized: "Stop", comment: "Chat toolbar: stops the run at work")
    case .confirmEdit: String(localized: "Save the queued message", comment: "Composer: confirms the edit of a queued message")
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
