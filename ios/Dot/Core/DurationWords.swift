import Foundation

/// How long something took, in the fewest characters (item 1393): "45s", "1m 12s", "2h 5m".
enum DurationWords {
    static func short(_ seconds: Double) -> String {
        let whole: Int = max(0, Int(seconds.rounded()))
        switch whole {
        case ..<60: return "\(whole)s"
        case ..<3600: return "\(whole / 60)m \(whole % 60)s"
        default: return "\(whole / 3600)h \((whole % 3600) / 60)m"
        }
    }

    /// What stands under a run that ended well: "Done · 3 steps · 1m 12s". Nil for a run that did not end done.
    static func finished(status: String, steps: Int, startedAt: Double, finishedAt: Double?) -> String? {
        guard status == "done", let finishedAt else { return nil }
        let count: String = steps == 1 ? "1 step" : "\(steps) steps"
        return steps > 0 ? "Done · \(count) · \(short(finishedAt - startedAt))" : "Done · \(short(finishedAt - startedAt))"
    }

    /// The line under the title for a chat at rest that finished a moment ago ("Done · 2 min ago", for ten minutes).
    static func subtitle(status: String, finishedAt: Double?, now: Date = Date()) -> String? {
        guard let finishedAt else { return nil }
        let age: Double = now.timeIntervalSince1970 - finishedAt
        guard age >= 0, age < 600 else { return nil }
        let ago: String = age < 60 ? "just now" : "\(Int(age / 60)) min ago"
        switch status {
        case "done": return "Done · \(ago)"
        case "failed": return "Failed · \(ago)"
        case "cancelled": return "Stopped · \(ago)"
        default: return nil
        }
    }
}
