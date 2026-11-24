import Foundation

/// What a helper's stored tool call says it does now, as the daemon's `step_of` (`thread_sync.py`) reads it: the tool and
/// a short target of its arguments. `line` stays empty: the app says the step in the owner's language from `tool`.
public func helperStep(of event: EventRecord) -> HelperStep? {
    guard event.kind == "tool_call", let tool: String = event.data["tool"]?.string else { return nil }
    var target: String = ""
    if let args: [String: JSONValue] = event.data["args"]?.object {
        for key in ["command", "url", "path", "file", "query", "goal", "name"] {
            if let value: String = args[key]?.string?.split(whereSeparator: \.isWhitespace).joined(separator: " "), !value.isEmpty {
                target = middleCut(value, 80)
                break
            }
        }
    }
    return HelperStep(tool: tool, target: target, line: "", at: event.ts)
}

/// `text` at most `limit` characters, the middle taken out with an ellipsis.
public func middleCut(_ text: String, _ limit: Int) -> String {
    guard text.count > limit, limit > 1 else { return text }
    let keep: Int = limit - 1
    let head: Int = (keep + 1) / 2
    return String(text.prefix(head)) + "…" + String(text.suffix(keep - head))
}
