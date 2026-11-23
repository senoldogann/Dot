import Foundation

/// How much of a tool step the detail sheet draws. The daemon already masks the secrets it holds in a step and clips a
/// result (`_EVENT_TEXT_CHARS`); the phone clips again so that a sheet never holds a wall of text: each argument, the
/// arguments together and the result have a limit, and what is left out is said, never silently dropped.
enum ToolDetailLimits {
    static let eachArgument: Int = 2_000
    static let allArguments: Int = 6_000
    static let result: Int = 8_000
}

/// Text cut to `limit` characters at a character boundary (never inside an emoji or an accent), and how much of it went.
struct Clipped: Equatable, Sendable {
    var text: String
    /// The number of characters left out; 0 when the text is whole.
    var omitted: Int

    var isCut: Bool { omitted > 0 }

    init(_ source: String, limit: Int) {
        // `prefix(_:)` walks characters (grapheme clusters), so a cut never splits one; only the cut part is counted.
        let kept: Substring = source.prefix(limit)
        text = String(kept)
        omitted = kept.endIndex == source.endIndex ? 0 : source[kept.endIndex...].count
    }
}

/// A tool step as its sheet shows it: what the tool was asked (its arguments) and what it answered, from the step's two
/// events (`tool_call`, `tool_result`).
struct ToolDetail: Equatable, Sendable {
    var tool: String
    var state: ToolRow.State
    /// The arguments as lines (`name: value`), clipped.
    var arguments: Clipped
    /// The result's text, clipped; `nil` while no result is stored (the step still runs, or its result is not kept).
    var result: Clipped?
    var seconds: Double?
    /// What the step touched, in the daemon's words (an address, a path), when it says.
    var target: String?

    /// The detail of `row` from its call event and its result event; either may be missing (a step still going has no
    /// result, an event that did not read has neither).
    static func make(row: ToolRow, call: EventRecord?, result: EventRecord?) -> ToolDetail {
        let rendered: String = call.map { renderArguments($0.data["args"]) } ?? ""
        let text: String? = result?.data["text"]?.string
        let target: String? = result?.data["target"]?.string.flatMap { $0.isEmpty ? nil : $0 }
        return ToolDetail(
            tool: row.tool, state: row.state,
            arguments: Clipped(rendered, limit: ToolDetailLimits.allArguments),
            result: text.map { Clipped($0, limit: ToolDetailLimits.result) },
            seconds: result?.data["seconds"]?.double, target: target)
    }

    /// Arguments as `name: value` lines in name order; a long or many-lined string goes under its name, indented.
    static func renderArguments(_ args: JSONValue?) -> String {
        guard let args else { return "" }
        guard let fields: [String: JSONValue] = args.object else { return compact(args, limit: ToolDetailLimits.eachArgument) }
        if fields.isEmpty { return "" }
        var lines: [String] = []
        for name in fields.keys.sorted() {
            guard let value: JSONValue = fields[name] else { continue }
            let shown: String
            if let words: String = value.string {
                let clip: Clipped = Clipped(words, limit: ToolDetailLimits.eachArgument)
                shown = clip.text + (clip.isCut ? "… (\(clip.omitted) more characters)" : "")
            } else {
                shown = compact(value, limit: ToolDetailLimits.eachArgument)
            }
            if shown.contains("\n") {
                lines.append("\(name):\n" + shown.split(separator: "\n", omittingEmptySubsequences: false).map { "  \($0)" }.joined(separator: "\n"))
            } else {
                lines.append("\(name): \(shown)")
            }
        }
        return lines.joined(separator: "\n")
    }

    private static func compact(_ value: JSONValue, limit: Int) -> String {
        let encoder: JSONEncoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let text: String = (try? encoder.encode(value)).map { String(decoding: $0, as: UTF8.self) } ?? ""
        let clip: Clipped = Clipped(text, limit: limit)
        return clip.text + (clip.isCut ? "… (\(clip.omitted) more characters)" : "")
    }
}

/// Finds a step's two events among those the daemon listed after the step's call.
enum ToolEvents {
    /// How many pages of events (each `pageSize`) are read after a step's call before its result is given up on: the
    /// result follows the call closely, and a step that has none stored is not worth reading a whole run for.
    static let pages: Int = 5
    static let pageSize: Int = 200

    static func call(_ callId: String, in events: [EventRecord]) -> EventRecord? {
        events.first { $0.kind == "tool_call" && $0.data["call_id"]?.string == callId }
    }

    static func result(_ callId: String, in events: [EventRecord]) -> EventRecord? {
        events.first { $0.kind == "tool_result" && $0.data["call_id"]?.string == callId }
    }
}
