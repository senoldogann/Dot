import DotEngine
import SwiftUI

/// While you were away (items 466, 1356, 1424): what Dot did on its own since the owner last marked it read on any
/// device, in one line; its newest runs and the runs with the most decisions made for the owner, two of each until
/// Show more; Mark as read moves the moment on for every device (the web's `away.js`).
struct AwayCard: View {
    let view: AwayView
    @Binding var showsAll: Bool
    let open: (String) -> Void
    /// Marks the whole of Dot's card read for every device; `nil` on an agent's profile, which shows a part of it.
    let markRead: (() async -> Void)?
    @Environment(\.locale) private var locale: Locale
    @State private var marking: Bool = false

    /// The runs, and the runs with the most decisions, shown before Show more (as the web's card).
    private let shownFirst: Int = 2

    var body: some View {
        GroupedSection(header: String(localized: "While you were away", comment: "Activity: the card of what Dot did on its own"), footer: nil) {
            Text(awayLine(view)).textRole(.body).foregroundStyle(Tone.text).frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.groupedGutter).padding(.top, Space.m).padding(.bottom, Space.xs)
            ForEach(showsAll ? view.runs : Array(view.runs.prefix(shownFirst))) { run in
                let outcome: RunOutcome = RunOutcome(status: run.status)
                ActivityItem(
                    symbol: outcome.symbol, title: run.title, line: runLine(run, outcome: outcome),
                    tone: outcome == .failed ? .failure : .neutral, opens: true
                ) { open(run.runId) }
            }
            if !view.decisionRuns.isEmpty {
                Text(String(localized: "Most decisions made for you", comment: "Away card: the runs with the most approvals answered for the owner"))
                    .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, Space.groupedGutter).padding(.top, Space.xs)
                ForEach(showsAll ? view.decisionRuns : Array(view.decisionRuns.prefix(shownFirst))) { run in
                    ActivityItem(
                        symbol: "checkmark.shield", title: run.title, line: decidedByWords(yolo: run.yolo, reviewer: run.reviewer, denied: run.denied),
                        tone: .neutral, opens: true
                    ) { open(run.runId) }
                }
            }
            HStack(spacing: Space.s) {
                let hidden: Int = max(0, view.runs.count - shownFirst) + max(0, view.decisionRuns.count - shownFirst)
                if !showsAll && hidden > 0 {
                    Button(String(localized: "Show \(hidden) more", comment: "Away card: shows the rest of its lists")) { showsAll = true }
                        .buttonStyle(SecondaryButtonStyle())
                }
                Spacer(minLength: 0)
                if let markRead {
                    Button {
                        marking = true
                        Task {
                            await markRead()
                            marking = false
                        }
                    } label: {
                        Text(String(localized: "Mark as read", comment: "Away card: moves the moment on for every device"))
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(marking)
                }
            }
            .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
        }
    }

    private func runLine(_ run: AwayRun, outcome: RunOutcome) -> String {
        let when: Double = run.finishedAt ?? run.createdAt
        var parts: [String] = [outcome.word, Date(timeIntervalSince1970: when).formatted(.relative(presentation: .named).locale(locale))]
        if !run.answer.isEmpty { parts.append(run.answer) }
        return parts.joined(separator: " · ")
    }
}
