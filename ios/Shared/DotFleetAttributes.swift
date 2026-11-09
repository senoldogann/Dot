import ActivityKit
import Foundation

/// The one Live Activity of the phone (item 1093): each of the owner's runs a row of it.
/// Shared by the app and the widget extension.
///
/// The daemon starts and updates it by push: `attributes-type` is `DotFleetAttributes`, and `attributes` and
/// `content-state` carry exactly these camelCase keys. Renaming a property breaks the daemon's payloads.
struct DotFleetAttributes: ActivityAttributes {
    /// One run as a row.
    struct Row: Codable, Hashable {
        var runId: String
        /// What the run is about, one line.
        var title: String
        /// Who works on it: Dot, or one of the owner's agents.
        var agent: String
        /// `working`, `waiting`, `done` or `failed`.
        var status: String
        /// What the run does now (item 1004): `thinking`, `browsing`, `command`…
        var kind: String
        /// The words of what it does now, or of how it ended.
        var line: String
        /// When the run started, in epoch seconds.
        var startedAt: Double
    }

    struct ContentState: Codable, Hashable {
        /// How many runs are at work, waiting for the owner or not.
        var active: Int
        /// The runs at work first, then those that ended last; at most four.
        var rows: [Row]
    }

    /// The key the daemon keeps the activity's token under: always `fleet`.
    var fleetId: String
}
