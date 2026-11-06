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
                    HStack(spacing: 6) {
                        LiveDotGlyph(size: 18)
                        Text(context.attributes.agentName)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    StatusTrailing(state: context.state)
                        .font(.subheadline)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(context.state.title)
                            .font(.subheadline.weight(.medium))
                            .lineLimit(1)
                        Text(lineText(context.state))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                LiveDotGlyph(size: 16)
            } compactTrailing: {
                StatusTrailing(state: context.state)
                    .font(.caption.monospacedDigit())
                    .frame(maxWidth: 52)
            } minimal: {
                LiveDotGlyph(size: 16)
                    .overlay(alignment: .bottomTrailing) {
                        if RunStatus(rawValue: context.state.status) == .waiting {
                            Circle().fill(.orange).frame(width: 6, height: 6)
                        }
                    }
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
                Text(lineText(state))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
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
        title: "Compare three flights to Izmir", status: "working", line: "Reading the second airline's page",
        startedAt: Date().addingTimeInterval(-95).timeIntervalSince1970)
    DotRunAttributes.ContentState(
        title: "Compare three flights to Izmir", status: "waiting", line: "",
        startedAt: Date().addingTimeInterval(-300).timeIntervalSince1970)
    DotRunAttributes.ContentState(
        title: "Compare three flights to Izmir", status: "done", line: "The cheapest is on Friday morning",
        startedAt: Date().addingTimeInterval(-600).timeIntervalSince1970)
}
