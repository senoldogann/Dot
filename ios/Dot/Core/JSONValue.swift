import Foundation

/// Any JSON value, for the `data` of an event, whose shape depends on its kind. Keys are kept as the daemon writes
/// them: none of this is decoded with a key strategy.
enum JSONValue: Codable, Sendable, Hashable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: any Decoder) throws {
        let single: any SingleValueDecodingContainer = try decoder.singleValueContainer()
        if single.decodeNil() {
            self = .null
        } else if let value: Bool = try? single.decode(Bool.self) {
            self = .bool(value)
        } else if let value: Int = try? single.decode(Int.self) {
            self = .int(value)
        } else if let value: Double = try? single.decode(Double.self) {
            self = .double(value)
        } else if let value: String = try? single.decode(String.self) {
            self = .string(value)
        } else if let value: [JSONValue] = try? single.decode([JSONValue].self) {
            self = .array(value)
        } else {
            self = .object(try single.decode([String: JSONValue].self))
        }
    }

    func encode(to encoder: any Encoder) throws {
        var single: any SingleValueEncodingContainer = encoder.singleValueContainer()
        switch self {
        case .null: try single.encodeNil()
        case .bool(let value): try single.encode(value)
        case .int(let value): try single.encode(value)
        case .double(let value): try single.encode(value)
        case .string(let value): try single.encode(value)
        case .array(let value): try single.encode(value)
        case .object(let value): try single.encode(value)
        }
    }

    subscript(key: String) -> JSONValue? {
        if case .object(let fields) = self { return fields[key] }
        return nil
    }

    var string: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var int: Int? {
        switch self {
        case .int(let value): return value
        case .double(let value): return Int(exactly: value)
        default: return nil
        }
    }

    var double: Double? {
        switch self {
        case .int(let value): return Double(value)
        case .double(let value): return value
        default: return nil
        }
    }

    var bool: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var array: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var object: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }
}
