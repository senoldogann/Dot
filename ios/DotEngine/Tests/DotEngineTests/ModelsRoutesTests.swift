import Foundation
import XCTest

@testable import DotEngine

/// Settings › Models' calls (item 1907): each sends what the daemon's route takes and reads its answer.
final class ModelsRoutesTests: XCTestCase {
    private var daemon: FakeDaemon!

    override func setUp() async throws { daemon = FakeDaemon(version: "1.0") }

    private func sent(_ method: String, _ path: String) throws -> [String: JSONValue] {
        try JSONDecoder().decode([String: JSONValue].self, from: try XCTUnwrap(daemon.bodies(method, path).last))
    }

    func testTheSecurityKindAndTheFallbacksAreWritten() async throws {
        let api: DaemonAPI = daemon.api()
        let kind: ModelsConfig = try await api.setKindModel("security", model: "m:deep")
        XCTAssertEqual(kind.kinds?["security"], "m:deep")
        XCTAssertEqual(try sent("PUT", "/api/models/kinds")["kind"]?.string, "security")
        let standby: ModelsConfig = try await api.setStandby(["m:one", "m:two"])
        XCTAssertEqual(standby.standby, ["m:one", "m:two"])
        XCTAssertEqual(try sent("PUT", "/api/models/standby")["models"]?.array?.compactMap(\.string), ["m:one", "m:two"])
    }

    func testTheLearningRolesTheLookAndSelfRepairAreReadAndWritten() async throws {
        let api: DaemonAPI = daemon.api()
        let learning: LearningModels = try await api.learningModels()
        XCTAssertEqual(learning.model(of: .composer), "m:compose")
        XCTAssertEqual(learning.model(of: .proposer), "", "plays on the review model")
        let set: LearningModels = try await api.setLearning(.selector, model: "m:pick")
        XCTAssertEqual(set.selector, "m:pick")
        let look: InitiativeLook = try await api.initiativeLook()
        XCTAssertEqual(look.lookModel, "")
        let looked: InitiativeLook = try await api.setLookModel("m:look")
        XCTAssertEqual(looked.lookModel, "m:look")
        let worker: RepairWorkerView = try await api.repairWorker()
        XCTAssertEqual(worker.worker, RepairWorker(model: "default", effort: "default", fast: false))
        let chosen: RepairWorkerView = try await api.setRepairWorker(RepairWorker(model: "opus", effort: "high", fast: true))
        XCTAssertEqual(chosen.worker, RepairWorker(model: "opus", effort: "high", fast: true))
        XCTAssertEqual(try sent("PUT", "/api/repair/worker")["fast"]?.bool, true)
    }

    func testAModelSaysWhetherItSeesAndAnOlderStatusHasNoKinds() throws {
        let option: ModelOption = try JSONDecoder().decode(
            ModelOption.self, from: Data(#"{"ref":"a:b","label":"b","efforts":[],"default_effort":null,"context_length":null,"vision":true}"#.utf8))
        XCTAssertEqual(option.vision, true)
        let older: ModelsConfig = try JSONDecoder().decode(
            ModelsConfig.self, from: Data(#"{"main":"a","vision":"b","fast":"c","review":"d","effort":"medium","helpers":[],"standby":[]}"#.utf8))
        XCTAssertNil(older.kinds)
    }
}
