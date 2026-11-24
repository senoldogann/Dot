import ActivityKit
import SwiftUI
import WidgetKit

@main
struct DotLiveActivityBundle: WidgetBundle {
    var body: some Widget {
        DotFleetLiveActivity()
    }
}

/// The app's colour tokens (`Shared/DotPalette.swift`), in the dark look the lock screen's glass sits on. They are the app's
/// neutral graphite family (item 1641): the ink for what is at work, a muted grey for what is done, amber for what waits on the
/// owner and a muted red for what failed. The activity stays static by choice, but it is not blue.
private extension Color {
    init(_ rgb: DotRGB) { self.init(red: rgb.red, green: rgb.green, blue: rgb.blue) }

    static let dotInk: Color = Color(DotPalette.dark.text)
    static let dotMuted: Color = Color(DotPalette.dark.muted)
    static let dotWarn: Color = Color(DotPalette.dark.warn)
    static let dotBad: Color = Color(DotPalette.dark.bad)
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
                // A tap on the card opens the chat of the run that waits for the owner, else the one at work.
                .widgetURL(context.state.tapURL)
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
                            RunLink(row: row)
                        }
                    }
                    // Kept off the island's rounded lower corners, which cut the line's first letter.
                    .padding(.horizontal, 12)
                    .padding(.bottom, 6)
                }
            } compactLeading: {
                // Held off the island's rounded ends, which cut a mark drawn to the edge (item 1098: the owner's
                // screenshot showed Dot's face and the trailing mark cut).
                LiveDotGlyph(size: 16)
                    .padding(.leading, 6)
            } compactTrailing: {
                DoingMark(state: context.state)
                    .padding(.trailing, 6)
            } minimal: {
                DoingMark(state: context.state)
                    .padding(2)
            }
            .keylineTint(Color.dotMuted)
            .widgetURL(context.state.tapURL)
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
                    RunLink(row: row)
                }
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
    }
}

/// A row that opens its own run's chat when tapped (`dotagent://run/<id>`).
private struct RunLink: View {
    let row: DotFleetAttributes.Row

    var body: some View {
        if let url: URL = DotDeepLink.url(forRun: row.runId) {
            Link(destination: url) { RowView(row: row) }
        } else {
            RowView(row: row)
        }
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
            // The status stays whole on one line (item 1113: "Working" wrapped as "Wor king"); the title and the detail
            // give way to it.
            StatusWord(row: row)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .layoutPriority(2)
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
            Text("Working").foregroundStyle(Color.dotInk)
        case .waiting:
            Text("Waiting").foregroundStyle(Color.dotWarn)
        case .done:
            Text("Done").foregroundStyle(Color.dotMuted)
        case .failed:
            Text(row.line == "Stopped" ? "Stopped" : "Failed").foregroundStyle(Color.dotBad)
        case nil:
            Text(row.status).foregroundStyle(.secondary)
        }
    }
}

/// The Dynamic Island's mark (item 1113, the owner's ask): what the newest run at work does now, as a tool's symbol
/// that pulses, orange while it waits for the owner; a check once none works. It said how many worked before.
private struct DoingMark: View {
    let state: DotFleetAttributes.ContentState

    var body: some View {
        let row: DotFleetAttributes.Row? = state.rows.first { (row: DotFleetAttributes.Row) in
            row.status == RunStatus.working.rawValue || row.status == RunStatus.waiting.rawValue
        }
        if let row {
            let waiting: Bool = row.status == RunStatus.waiting.rawValue
            Image(systemName: waiting ? "hand.raised.fill" : doingSymbol(row.kind))
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(waiting ? Color.dotWarn : Color.dotInk)
                .symbolEffect(.pulse, options: .repeating, isActive: !waiting)
                .contentTransition(.symbolEffect(.replace))
                .accessibilityLabel(row.line)
        } else {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.dotMuted)
        }
    }
}

/// The SF Symbol of what a run does now, as the daemon names it (item 1004).
private func doingSymbol(_ kind: String) -> String {
    switch kind {
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
    case "working": "gearshape"
    default: "ellipsis"
    }
}

/// Dot's mark, drawn small: a flat graphite disc with two eyes in the ink (the app's `DotGlyph`).
private struct LiveDotGlyph: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle().fill(Color(DotPalette.dark.raised))
            Circle().strokeBorder(Color.white.opacity(0.12), lineWidth: max(0.5, size * 0.03))
            HStack(spacing: size * 0.15) {
                Capsule().fill(Color.dotInk).frame(width: size * 0.11, height: size * 0.21)
                Capsule().fill(Color.dotInk).frame(width: size * 0.11, height: size * 0.21)
            }
            .offset(y: -size * 0.02)
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
