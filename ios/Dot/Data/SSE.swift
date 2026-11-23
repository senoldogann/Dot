import Foundation

/// One message of an event stream: its `event` name, its `data` lines joined by a newline, and its `id` if it had one.
struct SSEMessage: Equatable, Sendable {
    var event: String?
    var data: String
    var id: String?
}

/// Reads a server-sent event stream out of whatever chunks the network hands over: a line, a UTF-8 character or a
/// `\r\n` may be cut anywhere, a comment line (`:`) carries no message, and the last id is kept as the standard says.
struct SSEParser: Sendable {
    private var pending: [UInt8] = []
    private var event: String?
    private var data: [String] = []
    private var id: String?
    /// The id of the last message that had one (the standard's "last event ID").
    private(set) var lastEventId: String?

    /// Feeds the next chunk; returns the messages it finished.
    mutating func feed(_ chunk: some Sequence<UInt8>) -> [SSEMessage] {
        pending.append(contentsOf: chunk)
        var messages: [SSEMessage] = []
        var start: Int = 0
        var at: Int = 0
        while at < pending.count {
            let byte: UInt8 = pending[at]
            guard byte == 0x0A || byte == 0x0D else {
                at += 1
                continue
            }
            // A `\r` that ends the chunk may be the first half of `\r\n`: wait for the next byte.
            if byte == 0x0D && at + 1 == pending.count { break }
            let line: ArraySlice<UInt8> = pending[start..<at]
            at += (byte == 0x0D && pending[at + 1] == 0x0A) ? 2 : 1
            start = at
            if let message: SSEMessage = take(line: line) { messages.append(message) }
        }
        pending.removeSubrange(0..<start)
        return messages
    }

    private mutating func take(line: ArraySlice<UInt8>) -> SSEMessage? {
        if line.isEmpty {
            defer {
                event = nil
                data = []
                id = nil
            }
            guard !data.isEmpty else { return nil }
            return SSEMessage(event: event, data: data.joined(separator: "\n"), id: id)
        }
        if line.first == 0x3A { return nil }  // a comment
        let text: String = String(decoding: line, as: UTF8.self)
        let field: String
        var value: String
        if let colon: String.Index = text.firstIndex(of: ":") {
            field = String(text[..<colon])
            value = String(text[text.index(after: colon)...])
            if value.hasPrefix(" ") { value.removeFirst() }
        } else {
            field = text
            value = ""
        }
        switch field {
        case "event": event = value
        case "data": data.append(value)
        case "id":
            if !value.contains("\0") {
                id = value
                lastEventId = value.isEmpty ? nil : value
            }
        default: break
        }
        return nil
    }
}
