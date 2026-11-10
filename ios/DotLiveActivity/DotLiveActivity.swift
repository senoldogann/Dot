import ActivityKit
import SwiftUI
import WidgetKit

@main
struct DotLiveActivityBundle: WidgetBundle {
    var body: some Widget {
        DotFleetLiveActivity()
    }
}

/// A run's status as the daemon names it; `nil` for a name this build does not know, shown as its raw text.
enum RunStatus: String {
    case working
    case waiting
    case done
    case failed
}

/// The phone's one Live Activity (item 1093): every run of the owner's a row, on glass, on the lock
/// screen and in the Dynamic Island.
struct DotFleetLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: DotFleetAttributes.self) { context in
            LockScreenView(state: context.state)
                // Clear: the lock screen draws the activity on its own glass, the wallpaper showing through.
                .activityBackgroundTint(.clear)
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 8) {
                        LiveDotGlyph(size: 22)
                        Text("Dot")
                            .font(.headline)
                    }
                    .padding(.leading, 6)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(countText(context.state))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 6)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(context.state.rows.prefix(3), id: \.runId) { row in
                            RowView(row: row)
                        }
                    }
                    // Kept off the island's rounded lower corners, which cut the line's first letter.
                    .padding(.horizontal, 12)
                    .padding(.bottom, 6)
                }
            } compactLeading: {
                LiveDotGlyph(size: 18)
                    .padding(.leading, 2)
            } compactTrailing: {
                CountMark(state: context.state)
                    .padding(.trailing, 2)
            } minimal: {
                CountMark(state: context.state)
            }
            .keylineTint(.yellow)
        }
    }
}

/// "2 at work", "All done" once every row has ended, or "Starting" while the app's new card waits for its run's row.
private func countText(_ state: DotFleetAttributes.ContentState) -> String {
    if state.rows.isEmpty {
        return "Starting"
    }
    return state.active > 0 ? "\(state.active) at work" : "All done"
}

private struct LockScreenView: View {
    let state: DotFleetAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                LiveDotGlyph(size: 22)
                Text("Dot")
                    .font(.headline)
                Spacer(minLength: 8)
                Text(countText(state))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.75))
                    .contentTransition(.numericText())
            }
            VStack(alignment: .leading, spacing: 8) {
                ForEach(state.rows, id: \.runId) { row in
                    RowView(row: row)
                }
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
    }
}

/// One run: its title, then what it does or who works on it, and its status at the right.
private struct RowView: View {
    let row: DotFleetAttributes.Row

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(row.title)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
                .layoutPriority(1)
            Text(detail(row))
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.6))
                .lineLimit(1)
                .contentTransition(.opacity)
            Spacer(minLength: 6)
            StatusWord(row: row)
                .font(.subheadline.weight(.semibold))
        }
        .accessibilityElement(children: .combine)
    }
}

/// Beside the title: what a run at work does now, and for the others who worked on it (none for Dot itself).
private func detail(_ row: DotFleetAttributes.Row) -> String {
    switch RunStatus(rawValue: row.status) {
    case .working:
        return row.line
    case .waiting:
        return row.line.isEmpty ? "Waiting for you" : row.line
    case .done, .failed, nil:
        return row.agent == "Dot" ? "" : row.agent
    }
}

/// The status in its colour: Working, Waiting, Done, Failed.
private struct StatusWord: View {
    let row: DotFleetAttributes.Row

    var body: some View {
        switch RunStatus(rawValue: row.status) {
        case .working:
            Text("Working").foregroundStyle(Color(red: 0.55, green: 0.78, blue: 1.0))
        case .waiting:
            Text("Waiting").foregroundStyle(.orange)
        case .done:
            Text("Done").foregroundStyle(.green)
        case .failed:
            Text(row.line == "Stopped" ? "Stopped" : "Failed").foregroundStyle(.red)
        case nil:
            Text(row.status).foregroundStyle(.secondary)
        }
    }
}

/// The Dynamic Island's mark: how many runs work, in Dot's yellow, or a check once all are done.
private struct CountMark: View {
    let state: DotFleetAttributes.ContentState

    var body: some View {
        if state.active > 0 {
            Text("\(state.active)")
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(Color(red: 1.0, green: 0.78, blue: 0.22))
                .contentTransition(.numericText())
        } else {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.green)
        }
    }
}

/// Dot's mark, drawn small: a yellow dot with two eyes.
private struct LiveDotGlyph: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle().fill(Color(red: 1.0, green: 0.78, blue: 0.22))
            HStack(spacing: size * 0.14) {
                Capsule().fill(Color(white: 0.12)).frame(width: size * 0.12, height: size * 0.22)
                Capsule().fill(Color(white: 0.12)).frame(width: size * 0.12, height: size * 0.22)
            }
            .offset(y: -size * 0.06)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

#Preview("Lock screen", as: .content, using: DotFleetAttributes(fleetId: "fleet")) {
    DotFleetLiveActivity()
} contentStates: {
    DotFleetAttributes.ContentState(
        active: 2,
        rows: [
            DotFleetAttributes.Row(
                runId: "run_a", title: "Compare three flights to Izmir", agent: "Dot", status: "working",
                kind: "browsing", line: "Browsing the web", startedAt: Date().addingTimeInterval(-95).timeIntervalSince1970),
            DotFleetAttributes.Row(
                runId: "run_b", title: "Tez sohbeti", agent: "Scout", status: "waiting", kind: "waiting", line: "",
                startedAt: Date().addingTimeInterval(-300).timeIntervalSince1970),
            DotFleetAttributes.Row(
                runId: "run_c", title: "Summarise the YouTube video", agent: "Dot", status: "done", kind: "done",
                line: "Done", startedAt: Date().addingTimeInterval(-600).timeIntervalSince1970),
        ])
}
