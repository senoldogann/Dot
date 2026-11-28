import Foundation
import XCTest

@testable import DotEngine

/// A chat's tasks on the phone (item 2013): the new fields of the contract decode, a task's state word, the capsule's
/// count and the toast of a routed message.
final class ChatTasksTests: XCTestCase {
    private func task(_ thread: String, _ title: String, state: String, status: String, own: Bool) -> ChatTaskView {
        ChatTaskView(threadId: thread, title: title, runId: "run_\(thread)", status: status, state: state, own: own)
    }

    private var tasks: TasksView {
        TasksView(
            chatId: "thr_chat", chatTitle: "Gistsmith form", cap: 5,
            tasks: [
                task("thr_chat", "Gistsmith form", state: "waits", status: "done", own: true),
                task("thr_lane", "Improve my x.com account", state: "working", status: "running", own: false),
                task("thr_old", "Book the garage", state: "ended", status: "cancelled", own: false),
            ])
    }

    func testTheNewFieldsDecodeAndAnOlderDaemonsViewStillDoes() throws {
        let record: ThreadRecord = try JSONDecoder().decode(
            ThreadRecord.self,
            from: Data(#"{"id":"thr_lane","title":"x.com","source":"ui","created_at":1,"updated_at":2,"agent_id":null,"lane_of":"thr_chat"}"#.utf8))
        XCTAssertEqual(record.laneOf, "thr_chat")
        let older: ThreadRecord = try JSONDecoder().decode(
            ThreadRecord.self, from: Data(#"{"id":"thr_1","title":"Chat","source":"ui","created_at":1,"updated_at":2,"agent_id":null}"#.utf8))
        XCTAssertNil(older.laneOf)
        let queue: QueueView = try JSONDecoder().decode(
            QueueView.self,
            from: Data(
                #"{"held":false,"messages":[],"beside_run_id":null,"beside_thread_id":null,"routed":{"kind":"queued","thread_id":"thr_lane","run_id":null,"message_id":"q_1"}}"#
                    .utf8))
        XCTAssertEqual(queue.routed, RoutedMessage(kind: "queued", threadId: "thr_lane", runId: nil, messageId: "q_1"))
        let view: TasksView = try JSONDecoder().decode(
            TasksView.self,
            from: Data(
                #"{"chat_id":"thr_chat","chat_title":"Form","cap":5,"tasks":[{"thread_id":"thr_chat","title":"Form","run_id":"run_1","status":"running","state":"working","own":true}]}"#
                    .utf8))
        XCTAssertEqual(view.tasks.first?.own, true)
        XCTAssertEqual(view.tasks.first?.runId, "run_1")
    }

    func testARowSaysWorkingWaitsOrHowItsRunEnded() {
        XCTAssertEqual(taskPhase(task("a", "A", state: "working", status: "running", own: false)), .working)
        XCTAssertEqual(taskPhase(task("a", "A", state: "waits", status: "done", own: false)), .waits)
        XCTAssertEqual(taskPhase(task("a", "A", state: "ended", status: "done", own: false)), .done)
        XCTAssertEqual(taskPhase(task("a", "A", state: "ended", status: "cancelled", own: false)), .stopped)
        XCTAssertEqual(taskPhase(task("a", "A", state: "ended", status: "failed", own: false)), .failed)
    }

    func testTheCapsuleCountsTheTasksThatWorkOrWait() {
        XCTAssertEqual(openTaskCount(tasks), 2, "the ended one is not counted")
        XCTAssertEqual(openTaskCount(TasksView(chatId: "thr_chat", chatTitle: "Chat", cap: 5, tasks: [])), 0)
        XCTAssertEqual(openTaskCount(nil), 0, "a daemon before tasks")
        XCTAssertTrue(hasTasks(tasks))
        XCTAssertFalse(hasTasks(nil))
    }

    func testTheToastSaysWhereAMessageWentAndNothingForTheTaskOnScreen() {
        XCTAssertEqual(
            routeNotice(RoutedMessage(kind: "new", threadId: "thr_lane", runId: "run_2", messageId: nil), shown: "thr_chat", tasks: tasks),
            .startedNew(title: "Improve my x.com account", thread: "thr_lane", run: "run_2"))
        XCTAssertEqual(
            routeNotice(RoutedMessage(kind: "queued", threadId: "thr_lane", runId: nil, messageId: "q_1"), shown: "thr_chat", tasks: tasks),
            .sentTo(title: "Improve my x.com account", thread: "thr_lane", message: "q_1", run: nil))
        XCTAssertEqual(
            routeNotice(RoutedMessage(kind: "continued", threadId: "thr_chat", runId: "run_3", messageId: nil), shown: "thr_lane", tasks: tasks),
            .sentTo(title: "Gistsmith form", thread: "thr_chat", message: nil, run: "run_3"))
        XCTAssertNil(
            routeNotice(RoutedMessage(kind: "steered", threadId: "thr_lane", runId: "run_2", messageId: nil), shown: "thr_lane", tasks: tasks),
            "it went to the task on screen")
        XCTAssertEqual(
            routeNotice(RoutedMessage(kind: "stopped", threadId: "thr_chat", runId: nil, messageId: nil), shown: "thr_chat", tasks: tasks), .stoppedAll)
        XCTAssertEqual(
            routeNotice(RoutedMessage(kind: "stopped", threadId: "thr_lane", runId: "run_2", messageId: nil), shown: "thr_chat", tasks: tasks),
            .stopped(title: "Improve my x.com account"))
        XCTAssertEqual(moveTargets(from: "thr_lane", tasks: tasks).map(\.threadId), ["thr_chat", "thr_old"])
        // A steer went into a run already at work on its own task: Move would stop that run, so nothing moves.
        let steered: RouteNotice? = routeNotice(
            RoutedMessage(kind: "steered", threadId: "thr_lane", runId: "run_2", messageId: nil), shown: "thr_chat", tasks: tasks)
        XCTAssertEqual(steered, .sentTo(title: "Improve my x.com account", thread: "thr_lane", message: nil, run: nil))
        XCTAssertNil(steered.flatMap(moveSource))
        XCTAssertEqual(
            moveSource(.sentTo(title: "x", thread: "thr_lane", message: "q_1", run: nil)),
            MoveSource(thread: "thr_lane", message: "q_1", run: nil, toNewTask: true))
        XCTAssertEqual(
            moveSource(.startedNew(title: "x", thread: "thr_lane", run: "run_2")),
            MoveSource(thread: "thr_lane", message: nil, run: "run_2", toNewTask: false))
        XCTAssertNil(moveSource(.stoppedAll))
    }

    func testAMoveToANewTaskSendsANullThread() throws {
        let body: String = String(decoding: DaemonAPI.json(MoveBody(toThreadId: nil)), as: UTF8.self)
        XCTAssertEqual(body, #"{"to_thread_id":null}"#, "the daemon requires the key")
        XCTAssertEqual(String(decoding: DaemonAPI.json(MoveBody(toThreadId: "thr_b")), as: UTF8.self), #"{"to_thread_id":"thr_b"}"#)
    }
}
