import Foundation

// Settings › Models on the phone, as the web's page has them (item 1907): the security research kind, the fallbacks, the
// night's learning, Dot's own work and self-repair; the routes are the daemon's own (`routes_system.py`,
// `routes_proactive.py`, `routes_repair.py`).

private struct KindBody: Encodable {
    let kind: String
    let model: String
}

private struct StandbyBody: Encodable { let models: [String] }

private struct LearningBody: Encodable {
    let role: String
    let model: String
}

private struct LookBody: Encodable { let model: String }

extension DaemonAPI {
    /// `PUT /api/models/kinds`: the model a run of `kind` starts on, "" for the main model.
    public func setKindModel(_ kind: String, model: String) async throws(DaemonRequestError) -> ModelsConfig {
        try await send("PUT", "/api/models/kinds", body: Self.json(KindBody(kind: kind, model: model)))
    }

    /// `PUT /api/models/standby`: the fallbacks, in their order.
    public func setStandby(_ models: [String]) async throws(DaemonRequestError) -> ModelsConfig {
        try await send("PUT", "/api/models/standby", body: Self.json(StandbyBody(models: models)))
    }

    public func learningModels() async throws(DaemonRequestError) -> LearningModels { try await get("/api/models/learning", query: []) }

    /// `PUT /api/models/learning`: a role's model, "" to play it on the review model.
    public func setLearning(_ role: LearningRole, model: String) async throws(DaemonRequestError) -> LearningModels {
        try await send("PUT", "/api/models/learning", body: Self.json(LearningBody(role: role.rawValue, model: model)))
    }

    public func initiativeLook() async throws(DaemonRequestError) -> InitiativeLook { try await get("/api/initiative", query: []) }

    /// `PUT /api/initiative/look-model`: the model that looks for Dot's own work, "" for the main model.
    public func setLookModel(_ model: String) async throws(DaemonRequestError) -> InitiativeLook {
        try await send("PUT", "/api/initiative/look-model", body: Self.json(LookBody(model: model)))
    }

    public func repairWorker() async throws(DaemonRequestError) -> RepairWorkerView { try await get("/api/repair", query: []) }

    /// `PUT /api/repair/worker`: self-repair's model, effort and fast mode, from its next session.
    public func setRepairWorker(_ worker: RepairWorker) async throws(DaemonRequestError) -> RepairWorkerView {
        try await send("PUT", "/api/repair/worker", body: Self.json(worker))
    }
}
