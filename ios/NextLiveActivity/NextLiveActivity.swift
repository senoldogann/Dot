import ActivityKit
import DotEngine
import SwiftUI
import WidgetKit

@main
struct NextLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: DotFleetAttributes.self) { context in
            VStack(alignment: .leading, spacing: 8) {
                Label("Dot", systemImage: "circle.inset.filled")
                    .font(.headline)
                rows(context.state)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .widgetURL(url(context.state))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.bottom) { rows(context.state) }
            } compactLeading: {
                Image(systemName: "circle.inset.filled")
            } compactTrailing: {
                Text(context.state.active, format: .number)
            } minimal: {
                Image(systemName: "circle.inset.filled")
            }
            .widgetURL(url(context.state))
        }
    }

    private func url(_ state: DotFleetAttributes.ContentState) -> URL? {
        state.tapRunId.flatMap(PhoneRunLink.url(forRun:))
    }

    private func line(_ row: DotFleetAttributes.Row) -> String {
        if row.kind == "working" { return String(localized: "Working", comment: "Live Activity: task working") }
        if row.kind == "waiting" { return String(localized: "Waiting for you", comment: "Live Activity: task needs the owner") }
        return row.line
    }

    @ViewBuilder
    private func rows(_ state: DotFleetAttributes.ContentState) -> some View {
        if state.rows.isEmpty {
            Text(String(localized: "Starting…", comment: "Live Activity: waiting for the first task update"))
                .font(.subheadline)
        }
        ForEach(state.rows.filter { $0.status == "working" || $0.status == "waiting" }, id: \.runId) { row in
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: row.title).font(.subheadline.weight(.semibold))
                    Text(verbatim: line(row)).font(.footnote).foregroundStyle(.secondary)
                }
                .lineLimit(2)
                Spacer(minLength: 8)
                Text(Date(timeIntervalSince1970: row.startedAt), style: .timer)
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
                    .frame(maxWidth: 80, alignment: .trailing)
            }
        }
    }
}
