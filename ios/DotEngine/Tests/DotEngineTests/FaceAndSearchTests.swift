import Foundation
import XCTest

@testable import DotEngine

/// The living face of a chat at work and the chips of the search (the owner's asks of 2026-11-27).
final class FaceAndSearchTests: XCTestCase {
    /// A minute of a face's poses, ten a second.
    private func minute(seed: UInt32) -> [FacePose] {
        (0..<600).map { livingPose(at: 1000 + Double($0) / 10, seed: seed) }
    }

    func testALivingFaceBobsGlancesBothWaysBlinksAndFlipsRarelyWithinItsBounds() {
        let poses: [FacePose] = minute(seed: 7)
        XCTAssertTrue(poses.allSatisfy { $0.bob <= 0 && $0.bob >= -(FaceRhythm.bobHeight + FaceRhythm.hopHeight) }, "it only rises, and not far")
        XCTAssertTrue(poses.contains { $0.bob < -FaceRhythm.bobHeight * 0.9 }, "it bobs")
        XCTAssertTrue(poses.contains { $0.glance < -FaceRhythm.glanceReach * 0.9 } && poses.contains { $0.glance > FaceRhythm.glanceReach * 0.9 }, "it looks left and right")
        XCTAssertTrue(poses.allSatisfy { abs($0.glance) <= FaceRhythm.glanceReach + 1e-9 })
        // A blink is shorter than the minute's step: one blink's period, a hundred poses a second.
        let blinking: [FacePose] = (0..<Int(FaceRhythm.blinkEvery * 100)).map { livingPose(at: 1000 + Double($0) / 100, seed: 7) }
        XCTAssertTrue(blinking.contains { $0.open < 0.5 }, "it blinks")
        XCTAssertTrue(poses.allSatisfy { $0.open > 0 && $0.open <= 1 })
        let flips: Int = zip(poses, poses.dropFirst()).filter { $0.turn == 0 && $1.turn > 0 }.count
        XCTAssertTrue((3...4).contains(flips), "a flip about every \(FaceRhythm.flipEvery) s: \(flips) in a minute")
        let turning: Int = poses.filter { $0.turn > 0 }.count
        XCTAssertLessThan(Double(turning) / Double(poses.count), 0.06, "it turns rarely")
        XCTAssertTrue(poses.allSatisfy { $0.turn >= 0 && $0.turn <= 360 })
    }

    func testTwoFacesAtWorkDoNotMoveAsOneAndAFaceIsTheSameAtTheSameMoment() {
        XCTAssertNotEqual(minute(seed: hueSeed("worker")), minute(seed: hueSeed("delegator")))
        XCTAssertEqual(livingPose(at: 42.5, seed: 9), livingPose(at: 42.5, seed: 9))
        XCTAssertEqual(FacePose.rest, FacePose(bob: 0, glance: 0, open: 1, turn: 0))
    }

    func testUnderReduceMotionTheFacePulsesInBrightnessOnly() {
        let pulse: [Double] = (0..<48).map { stillPulse(at: Double($0) / 20) }
        XCTAssertEqual(pulse.max() ?? 0, 1, accuracy: 0.01)
        XCTAssertEqual(pulse.min() ?? 0, FaceRhythm.pulseLow, accuracy: 0.01)
    }

    private func hueSeed(_ id: String) -> UInt32 {
        id.utf16.reduce(UInt32(0x811c_9dc5)) { ($0 ^ UInt32($1)) &* 0x0100_0193 }
    }

    private func finds() -> HomeFinds {
        let summary: (String) -> ThreadRow = { id in
            ThreadRow(
                summary: ThreadSummary(
                    id: id, title: "Tax \(id)", source: "ui", createdAt: 1, updatedAt: 1, pinnedAt: nil, runCount: 1, latestRunId: "r-\(id)",
                    latestStatus: "done", latestCreatedAt: 1, unread: false, actorId: nil, lastMessage: "", lastRole: "assistant"),
                revision: 1, asks: 0, helpers: nil, background: nil)
        }
        let file: (String) -> FileHit = { name in FileHit(threadId: "c1", runId: "r", kind: "made", name: name, path: "~/\(name)", attachmentId: nil, at: 1) }
        return HomeFinds(
            bots: [], chats: (1...4).map { summary("c\($0)") },
            messages: [ChatHit(threadId: "c9", seq: 3, kind: "m", ref: "user", snippet: "the [tax] form")],
            files: ["tax.pdf", "tax.md"].map(file))
    }

    func testEachChipShowsItsKindAndAllShowsAFewOfEach() {
        let found: HomeFinds = finds()
        let all: HomeFinds = scopedFinds(found, scope: .all)
        XCTAssertEqual(all.chats.count, searchAllShown, "All shows a few of each kind")
        XCTAssertEqual(all.files.count, 2)
        XCTAssertEqual(all.messages.count, 1)
        XCTAssertEqual(cutShort(found), [.messages], "Messages' chip shows the chat All cut")
        let messages: HomeFinds = scopedFinds(found, scope: .messages)
        XCTAssertEqual(messages.chats.count, 4)
        XCTAssertEqual(messages.messages.count, 1)
        XCTAssertTrue(messages.files.isEmpty && messages.bots.isEmpty)
        let files: HomeFinds = scopedFinds(found, scope: .files)
        XCTAssertEqual(files.files.map(\.name), ["tax.pdf", "tax.md"])
        XCTAssertTrue(files.chats.isEmpty && files.messages.isEmpty)
        XCTAssertTrue(scopedFinds(found, scope: .bots).isEmpty, "no bot holds the words")
        XCTAssertEqual(SearchScope.allCases, [.all, .messages, .bots, .files])
    }

    func testAMessageFindOpensItsChatAtTheMarkedWord() {
        XCTAssertEqual(markedWord("we booked the [Lastikçi] garage"), "Lastikçi")
        XCTAssertEqual(markedWord("...[tax] and [form]"), "tax", "the first mark")
        XCTAssertNil(markedWord("no mark here"))
        XCTAssertNil(markedWord("an empty [] mark"))
    }

    func testAFileFindReadsTheDaemonsWords() throws {
        let json: Data = Data(#"[{"thread_id":"t1","run_id":"r1","kind":"attachment","name":"a.pdf","path":"a.pdf","attachment_id":"att_1_ab","at":5.5}]"#.utf8)
        let hits: [FileHit] = try JSONDecoder().decode([FileHit].self, from: json)
        XCTAssertEqual(hits, [FileHit(threadId: "t1", runId: "r1", kind: "attachment", name: "a.pdf", path: "a.pdf", attachmentId: "att_1_ab", at: 5.5)])
    }
}
