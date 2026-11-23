import Foundation

/// The events of each run that could not be read, by id: a set, so that the same event handed over again (a reconnect, a
/// later catch-up that meets it again) is counted once. An event whose id could not be read either is `unknownId`, once
/// for all.
struct UnreadableEvents: Codable, Sendable, Equatable {
    static let unknownId: Int = -1
    private(set) var byRun: [String: Set<Int>] = [:]

    /// Adds the ids; how many of them were new.
    @discardableResult
    mutating func add(_ ids: [Int], run: String) -> Int {
        let before: Int = byRun[run]?.count ?? 0
        byRun[run, default: []].formUnion(ids)
        return (byRun[run]?.count ?? 0) - before
    }

    func count(run: String) -> Int { byRun[run]?.count ?? 0 }

    var isEmpty: Bool { byRun.isEmpty }
}
