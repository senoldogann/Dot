import ActivityKit
import Foundation

/// The Live Activity of one of the owner's runs, shared by the app and the widget extension.
///
/// The daemon starts and updates it by push: `attributes-type` is `DotRunAttributes`, and `attributes` and
/// `content-state` carry exactly these camelCase keys. Renaming a property breaks the daemon's payloads.
struct DotRunAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        /// What the run is about, one line.
        var title: String
        /// `working`, `waiting`, `done` or `failed`.
        var status: String
        /// What the run does now (item 1004): `thinking`, `browsing`, `command`… which picks the mark beside `line`;
        /// `nil` from a daemon older than that.
        var kind: String?
        /// The run's current status line (the step, or what it waits for).
        var line: String
        /// When the run started, in epoch seconds.
        var startedAt: Double
    }

    var runId: String
    var agentName: String
}
