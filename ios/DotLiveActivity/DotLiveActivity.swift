import ActivityKit
import SwiftUI
import WidgetKit

@main
struct DotLiveActivityBundle: WidgetBundle {
    var body: some Widget {
        DotRunLiveActivity()
    }
}

/// A run's status as the daemon names it; `nil` for a name this build does not know, shown as its raw text.
enum RunStatus: String {
    case working
    case waiting
    case done
    case failed
}

/// The Live Activity of one of the owner's runs: lock screen, Dynamic Island compact, minimal and expanded.
struct DotRunLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: DotRunAttributes.self) { context in
            LockScreenView(attributes: context.attributes, state: context.state)
                .activityBackgroundTint(nil)
                .activitySystemActionForegroundColor(.primary)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 8) {
                        LiveDotGlyph(size: 22)
                        Text(context.attributes.agentName)
                            .font(.headline)
                            .lineLimit(1)
                    }
                    .padding(.leading, 6)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    StatusTrailing(state: context.state)
                        .font(.subheadline)
                        .padding(.trailing, 6)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(context.state.title)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                        DoingRow(state: context.state)
                            .font(.subheadline)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    // Kept off the island's rounded lower corners, which cut the line's first letter.
                    .padding(.horizontal, 12)
                    .padding(.bottom, 6)
                }
            } compactLeading: {
                LiveDotGlyph(size: 18)
                    .padding(.leading, 2)
            } compactTrailing: {
                DoingMark(state: context.state)
                    .font(.system(size: 14, weight: .semibold))
                    .padding(.trailing, 2)
            } minimal: {
                DoingMark(state: context.state)
                    .font(.system(size: 13, weight: .semibold))
            }
            .keylineTint(.yellow)
        }
    }
}

private struct LockScreenView: View {
    let attributes: DotRunAttributes
    let state: DotRunAttributes.ContentState

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            LiveDotGlyph(size: 28)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(attributes.agentName)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Spacer()
                    StatusTrailing(state: state)
                        .font(.subheadline)
                }
                Text(state.title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                DoingRow(state: state)
                    .font(.subheadline)
            }
        }
        .padding(16)
    }
}

/// The right-hand side: a running timer while working, a word for the other states.
private struct StatusTrailing: View {
    let state: DotRunAttributes.ContentState

    var body: some View {
        switch RunStatus(rawValue: state.status) {
        case .working:
            Text(Date(timeIntervalSince1970: state.startedAt), style: .timer)
                .monospacedDigit()
                .multilineTextAlignment(.trailing)
                .foregroundStyle(.secondary)
        case .waiting:
            Label("Waiting", systemImage: "hand.raised.fill")
                .labelStyle(.titleAndIcon)
                .foregroundStyle(.orange)
        case .done:
            Label("Done", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed:
            Label("Failed", systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red)
        case nil:
            Text(state.status)
                .foregroundStyle(.secondary)
        }
    }
}

/// What the run does now as the daemon names it (item 1004), and the SF Symbol drawn for it.
private func doingSymbol(_ state: DotRunAttributes.ContentState) -> String {
    switch state.kind ?? state.status {
    case "thinking": "brain"
    case "answering": "text.cursor"
    case "browsing": "globe"
    case "searching": "magnifyingglass"
    case "command": "terminal"
    case "reading": "doc.text.magnifyingglass"
    case "writing": "square.and.pencil"
    case "mac": "macbook"
    case "mail": "envelope"
    case "calendar": "calendar"
    case "memory": "books.vertical"
    case "helpers": "person.2"
    case "app": "square.grid.2x2"
    case "trading": "chart.line.uptrend.xyaxis"
    case "waiting": "hand.raised.fill"
    case "done": "checkmark.circle.fill"
    case "failed": "xmark.octagon.fill"
    default: "ellipsis"
    }
}

/// The colour of the mark: Dot's yellow at work, orange while it waits, green done, red failed.
private func doingColor(_ state: DotRunAttributes.ContentState) -> Color {
    switch RunStatus(rawValue: state.status) {
    case .waiting: .orange
    case .done: .green
    case .failed: .red
    case .working, nil: Color(red: 1.0, green: 0.78, blue: 0.22)
    }
}

/// The mark of what the run does now. A Live Activity plays no repeating animation, only the change from one update to
/// the next, so the mark turns into the next one as the run moves on.
private struct DoingMark: View {
    let state: DotRunAttributes.ContentState

    var body: some View {
        Image(systemName: doingSymbol(state))
            .foregroundStyle(doingColor(state))
            .contentTransition(.symbolEffect(.replace))
            .accessibilityHidden(true)
    }
}

/// The mark and the line of what the run does now, side by side.
private struct DoingRow: View {
    let state: DotRunAttributes.ContentState

    var body: some View {
        HStack(spacing: 8) {
            DoingMark(state: state)
                .frame(width: 18)
            Text(lineText(state))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .contentTransition(.opacity)
        }
    }
}

/// The status line, or "Waiting for you" when the run waits and the daemon sent no line.
private func lineText(_ state: DotRunAttributes.ContentState) -> String {
    if RunStatus(rawValue: state.status) == .waiting && state.line.isEmpty {
        return "Waiting for you"
    }
    return state.line
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

#Preview("Lock screen", as: .content, using: DotRunAttributes(runId: "run_preview", agentName: "Dot")) {
    DotRunLiveActivity()
} contentStates: {
    DotRunAttributes.ContentState(
        title: "Compare three flights to Izmir", status: "working", kind: "browsing", line: "Browsing the web",
        startedAt: Date().addingTimeInterval(-95).timeIntervalSince1970)
    DotRunAttributes.ContentState(
        title: "Compare three flights to Izmir", status: "waiting", kind: "waiting", line: "",
        startedAt: Date().addingTimeInterval(-300).timeIntervalSince1970)
    DotRunAttributes.ContentState(
        title: "Compare three flights to Izmir", status: "done", kind: "done", line: "Done",
        startedAt: Date().addingTimeInterval(-600).timeIntervalSince1970)
}
