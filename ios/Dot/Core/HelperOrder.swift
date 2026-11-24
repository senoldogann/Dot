import Foundation

/// The helpers of a chat in the order the app lists them (item 1467). The list used to run in the order the helpers came, so that
/// five helpers at work stood below sixty that had ended and were out of sight (the owner's screenshot of 2026-11-24, 10:42):
/// the ones at work come first, newest first, and the finished ones after them, newest first. The run inspector (the Run
/// sheet, which the capsule's helpers segment opens) draws exactly these two groups, each with its count in its header.
enum HelperOrder {
    enum Group: Sendable {
        case working, finished
    }

    struct Groups: Equatable, Sendable {
        var working: [Helper]
        var finished: [Helper]

        var isEmpty: Bool { working.isEmpty && finished.isEmpty }
    }

    /// Splits the helpers into those at work (queued, running, waiting on the owner, ending) and those that ended, each newest
    /// first by the time the daemon queued it. One whose time this state has not heard (0) stands where it came: the later
    /// arrival is the newer.
    static func groups(_ helpers: [Helper]) -> Groups {
        let newestFirst: [Helper] = helpers.enumerated()
            .sorted { left, right in
                left.element.createdAt != right.element.createdAt ? left.element.createdAt > right.element.createdAt : left.offset > right.offset
            }
            .map(\.element)
        return Groups(working: newestFirst.filter(\.isLive), finished: newestFirst.filter { !$0.isLive })
    }

    /// The header of a group, with how many it holds: "Working · 5", "Finished · 12".
    static func title(_ group: Group, count: Int) -> String {
        switch group {
        case .working: "Working · \(count)"
        case .finished: "Finished · \(count)"
        }
    }

    /// The words on a helper's row: how it stands in the daemon's own word, "Running", "Done", "Failed", "Cancelled"; one that
    /// waits on the owner says "Waiting on you" (the daemon's word for it is "blocked"), one that is ending "Stopping".
    static func badge(for helper: Helper) -> String {
        switch helper.status {
        case "blocked": "Waiting on you"
        case "ending": "Stopping"
        default: helper.status.capitalizedFirst
        }
    }
}
