import Foundation
import XCTest

@testable import DotEngine

/// What the phone does with notes beyond reading them: add one, pin or unpin it, forget it and bring it back from the
/// trash. Each call reaches the daemon's route with its method and body, and decodes what the daemon answers.
final class MemoryNotesTests: XCTestCase {
    private static let host: String = "memory.test"

    override func setUp() {
        super.setUp()
        ActivityDaemon.reset()
    }

    private func api() -> DaemonAPI {
        let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ActivityDaemon.self]
        let session: URLSession = URLSession(configuration: configuration)
        return DaemonAPI(host: DaemonHost(name: Self.host, origin: URL(string: "https://\(Self.host)")!), key: "k", session: session, streamSession: session)
    }

    private func record(_ id: String, pinned: Double?) -> String {
        let pin: String = pinned.map { String($0) } ?? "null"
        return
            #"{"id":"\#(id)","content":"Invoices live in ~/Documents.","tags":["work","money"],"created_at":1,"updated_at":2,"source_run":null,"agent_id":null,"used_count":0,"last_used_at":null,"expires_at":null,"replaced_by":null,"tainted":false,"merged_into":null,"merged_from":[],"pinned_at":\#(pin),"rested_at":null,"edited_by":null,"proposed":null}"#
    }

    private func body(_ line: String) throws -> [String: Any] {
        let seen: ActivityDaemon.Seen = try XCTUnwrap(ActivityDaemon.seen.last { $0.line == line })
        return try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(seen.body)) as? [String: Any])
    }

    func testANoteIsAddedWithItsWordsAndTags() async throws {
        ActivityDaemon.answer("POST /api/memories", 201, #"{"record":\#(record("m1", pinned: nil)),"created":true,"similar":[]}"#)
        let saved: SavedNote = try await api().addMemory(MemoryWrite(content: "Invoices live in ~/Documents.", tags: ["work", "money"]))
        XCTAssertTrue(saved.created)
        XCTAssertEqual(saved.record.tags, ["work", "money"])
        let sent: [String: Any] = try body("POST /api/memories")
        XCTAssertEqual(sent["content"] as? String, "Invoices live in ~/Documents.")
        XCTAssertEqual(sent["tags"] as? [String], ["work", "money"])
    }

    func testAPinIsPutAndTakenOffWithTheBoolTheDaemonRequires() async throws {
        ActivityDaemon.answer("PUT /api/memories/m1/pin", 200, record("m1", pinned: 5))
        let pinned: MemoryNote = try await api().pinMemory("m1", pinned: true)
        XCTAssertEqual(pinned.pinnedAt, 5)
        XCTAssertEqual(try body("PUT /api/memories/m1/pin")["pinned"] as? Bool, true)
        ActivityDaemon.answer("PUT /api/memories/m1/pin", 200, record("m1", pinned: nil))
        let loose: MemoryNote = try await api().pinMemory("m1", pinned: false)
        XCTAssertNil(loose.pinnedAt)
        XCTAssertEqual(try body("PUT /api/memories/m1/pin")["pinned"] as? Bool, false)
    }

    func testTheTrashDecodesAndANoteComesBackFromIt() async throws {
        ActivityDaemon.answer(
            "GET /api/memories/trash", 200,
            #"{"items":[{"record":\#(record("m2", pinned: nil)),"trashed_at":100.5,"purged_at":2592100.5}],"total":1,"trash_days":30}"#)
        let trash: MemoryTrash = try await api().memoryTrash()
        XCTAssertEqual(trash.total, 1)
        XCTAssertEqual(trash.trashDays, 30)
        XCTAssertEqual(trash.items.first?.id, "m2")
        XCTAssertEqual(trash.items.first?.purgedAt, 2_592_100.5)
        ActivityDaemon.answer("POST /api/memories/trash/m2/restore", 200, record("m2", pinned: nil))
        let back: MemoryNote = try await api().restoreMemory("m2")
        XCTAssertEqual(back.id, "m2")
        ActivityDaemon.answer("DELETE /api/memories/m2", 200, record("m2", pinned: nil))
        let gone: MemoryNote = try await api().forgetMemory("m2")
        XCTAssertEqual(gone.id, "m2")
        XCTAssertTrue(ActivityDaemon.seen.contains { $0.line == "DELETE /api/memories/m2" })
    }
}
