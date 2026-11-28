import DotEngine
import XCTest

@testable import DotNext

/// The words of a chat's tasks (item 2013): each state of a row has its own, the chat's own row is "This chat", and the
/// keys whose English is another key's ("Open", "Stopped", whose Turkish differs there) read right in both languages.
final class ChatTasksWordsTests: XCTestCase {
    private func bundle(_ language: String) throws -> Bundle {
        let path: String = try XCTUnwrap(Bundle(for: AppBundleAnchor.self).path(forResource: language, ofType: "lproj"))
        return try XCTUnwrap(Bundle(path: path))
    }

    func testEachRowStateHasItsOwnWordsAndTheChatsOwnRowIsThisChat() {
        let phases: [TaskPhase] = [.working, .waits, .done, .stopped, .failed]
        let words: [String] = phases.map(taskPhaseWords)
        XCTAssertEqual(Set(words).count, phases.count, "\(words)")
        XCTAssertFalse(words.contains("task.stopped"), "the key is never shown")
        let own: ChatTaskView = ChatTaskView(threadId: "thr_1", title: "Form", runId: "run_1", status: "running", state: "working", own: true)
        let lane: ChatTaskView = ChatTaskView(threadId: "thr_2", title: "x.com", runId: "run_2", status: "running", state: "working", own: false)
        XCTAssertEqual(taskTitleWords(own), String(localized: "This chat", comment: "Title of the run inspector"))
        XCTAssertEqual(taskTitleWords(lane), "x.com")
        XCTAssertTrue(routeNoticeWords(.sentTo(title: "x.com", thread: "thr_2", message: "q_1", run: nil)).contains("x.com"))
    }

    func testTheTasksWordsInEnglishAndTurkish() throws {
        let english: Bundle = try bundle("en")
        XCTAssertEqual(english.localizedString(forKey: "task.stopped", value: nil, table: nil), "Stopped")
        XCTAssertEqual(english.localizedString(forKey: "task.open", value: nil, table: nil), "Open")
        let turkish: Bundle = try bundle("tr")
        XCTAssertEqual(turkish.localizedString(forKey: "task.stopped", value: nil, table: nil), "Durdu")
        XCTAssertEqual(turkish.localizedString(forKey: "task.open", value: nil, table: nil), "Aç")
        XCTAssertEqual(turkish.localizedString(forKey: "Waits for you", value: nil, table: nil), "Seni bekliyor")
        XCTAssertEqual(turkish.localizedString(forKey: "Sent to %@", value: nil, table: nil), "%@ görevine gönderildi")
    }
}
