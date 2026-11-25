import Foundation

// The routes of Settings › Agents › Campaigns on the phone (item 1895, `routes_campaigns.py`): the list, a campaign, and
// what the owner does to it. Each type holds the part the phone shows; JSON keys it does not name are ignored.

/// A campaign: a goal Dot keeps working on, one session at a time, until a check confirms it.
public struct CampaignRecord: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let title: String
    public let goal: String
    public let status: String
    public let targets: String
    public let verification: String
    public let evidence: String
    public let model: String
    public let approvals: String
    public let createdAt: Double

    enum CodingKeys: String, CodingKey {
        case id, title, goal, status, targets, verification, evidence, model, approvals
        case createdAt = "created_at"
    }
}

/// A note a session of a campaign wrote: progress, a setback, a strategy, a question or a note.
public struct CampaignEntry: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let runId: String?
    public let ts: Double
    public let kind: String
    public let text: String
    public let numbers: [String: Double]

    enum CodingKeys: String, CodingKey {
        case id, ts, kind, text, numbers
        case runId = "run_id"
    }
}

/// `GET /api/campaigns`: one row of the list.
public struct CampaignSummary: Codable, Sendable, Hashable, Identifiable {
    public let record: CampaignRecord
    public let sessions: Int
    public let working: String?
    public let nextLocal: String?
    public let progress: CampaignEntry?

    public var id: String { record.id }

    enum CodingKeys: String, CodingKey {
        case record, sessions, working, progress
        case nextLocal = "next_local"
    }
}

/// A session of a campaign: one run.
public struct CampaignSession: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let status: String
    public let createdAt: Double
    public let result: String?
    public let inputTokens: Int
    public let outputTokens: Int

    enum CodingKeys: String, CodingKey {
        case id, status, result
        case createdAt = "created_at"
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
    }
}

/// A question a session asked the owner.
public struct CampaignQuestion: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let runId: String
    public let createdAt: Double
    public let status: String
    public let summary: String
    public let note: String

    enum CodingKeys: String, CodingKey {
        case id, status, summary, note
        case runId = "run_id"
        case createdAt = "created_at"
    }
}

/// `GET /api/campaigns/<id>` and the answer of its actions.
public struct CampaignDetail: Codable, Sendable, Hashable {
    public let record: CampaignRecord
    public let sessions: [CampaignSession]
    public let working: String?
    public let nextLocal: String?
    public let entries: [CampaignEntry]
    public let questions: [CampaignQuestion]
    public let tokens: Int

    enum CodingKeys: String, CodingKey {
        case record, sessions, working, entries, questions, tokens
        case nextLocal = "next_local"
    }
}

/// What the owner does to a campaign (`POST /api/campaigns/<id>/<action>`).
public enum CampaignAction: String, Sendable, CaseIterable {
    /// No session starts after it, and the one working now is stopped; Resume takes it back.
    case stop
    /// A stopped or achieved campaign goes on: its next session starts with the scheduler's next tick.
    case resume
    /// The owner's word closes it as achieved, without the goal check.
    case achieved
}

extension CampaignRecord {
    /// What the daemon takes for this campaign now (`agent/campaign_control.py`, which answers anything else with 409):
    /// Stop while it is active, Resume once it is not, and Mark achieved until it is achieved.
    public var actions: [CampaignAction] {
        [status == "active" ? .stop : .resume] + (status == "achieved" ? [] : [.achieved])
    }
}

