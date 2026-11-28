import Foundation
import XCTest

@testable import DotEngine

/// The app's side of the frozen contract (plan §8, §9: "a schema test on both sides"): the daemon's
/// `docs/schemas/thread-sync.schema.json` is read, and the smallest and the fullest instance it allows of each shape the
/// engine reads are decoded into the engine's types. A field the engine requires that the schema does not, or a type
/// that does not match, fails here before it fails on the phone.
final class ContractSchemaTests: XCTestCase {
    private typealias Object = [String: Any]

    private static func schema() throws -> Object {
        let file: URL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appending(path: "docs/schemas/thread-sync.schema.json")
        let data: Data = try Data(contentsOf: file)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? Object)
    }

    /// An instance of `node`: with `full`, every property and the non-null branch of each choice; otherwise only the
    /// required ones and `null` where allowed.
    private static func instance(_ node: Object, defs: Object, full: Bool, depth: Int) throws -> Any {
        if let ref: String = node["$ref"] as? String {
            let name: String = String(ref.split(separator: "/").last ?? "")
            let target: Object = try XCTUnwrap(defs[name] as? Object, "no definition \(name)")
            return try instance(target, defs: defs, full: full, depth: depth + 1)
        }
        if let choices: [Object] = node["anyOf"] as? [Object] {
            let nonNull: Object? = choices.first { ($0["type"] as? String) != "null" }
            let allowsNull: Bool = choices.contains { ($0["type"] as? String) == "null" }
            if !full && allowsNull || nonNull == nil || depth > 6 { return NSNull() }
            return try instance(nonNull!, defs: defs, full: full, depth: depth + 1)
        }
        if let constant: Any = node["const"] { return constant }
        if let values: [Any] = node["enum"] as? [Any], let first: Any = values.first { return first }
        switch node["type"] as? String {
        case "string": return "x"
        case "integer": return 1
        case "number": return 1.5
        case "boolean": return false
        case "array":
            guard full, depth <= 6, let items: Object = node["items"] as? Object else { return [Any]() }
            return [try instance(items, defs: defs, full: full, depth: depth + 1)]
        case "object", nil:
            let properties: Object = node["properties"] as? Object ?? [:]
            let required: Set<String> = Set(node["required"] as? [String] ?? [])
            var out: Object = [:]
            for (name, value) in properties where full || required.contains(name) {
                out[name] = try instance(try XCTUnwrap(value as? Object), defs: defs, full: full, depth: depth + 1)
            }
            return out
        default:
            return NSNull()
        }
    }

    private func check<T: Decodable>(_ type: T.Type, _ name: String) throws {
        let root: Object = try Self.schema()
        let defs: Object = try XCTUnwrap(root["$defs"] as? Object)
        let node: Object = try XCTUnwrap(defs[name] as? Object, "the contract has no \(name)")
        for full in [false, true] {
            let value: Any = try Self.instance(node, defs: defs, full: full, depth: 0)
            let data: Data = try JSONSerialization.data(withJSONObject: value)
            XCTAssertNoThrow(try JSONDecoder().decode(T.self, from: data), "\(name) (\(full ? "fullest" : "smallest")) does not decode")
        }
    }

    func testEveryShapeTheEngineReadsDecodesFromTheSmallestAndTheFullestInstanceTheContractAllows() throws {
        try check(ThreadView.self, "ThreadView")
        try check(ThreadsView.self, "ThreadsView")
        try check(EventRecord.self, "EventRecord")
        try check(ThreadChanged.self, "ThreadChanged")
        try check(Synchronized.self, "Synchronized")
        try check(HelperView.self, "HelperView")
        try check(QueueView.self, "QueueView")
        try check(RunRecord.self, "RunRecord")
        try check(ApprovalRecord.self, "ApprovalRecord")
        try check(TasksView.self, "TasksView")
        try check(ChatTaskView.self, "ChatTaskView")
        try check(RoutedMessage.self, "RoutedMessage")
        try check(ThreadRecord.self, "ThreadRecord")
    }

    func testTheContractStillNamesTheStreamsOwnKindsAndTheFiveSecondKeepalive() throws {
        let comment: String = try XCTUnwrap(Self.schema()["$comment"] as? String)
        XCTAssertTrue(comment.contains("thread_changed") && comment.contains("synchronized"))
        XCTAssertTrue(comment.contains("keepalive every 5 s"), "the 12 s silence limit is built on it")
    }
}
