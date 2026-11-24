import DotEngine
import SwiftUI
import UIKit

/// The iPad decision of wave N0 (review S9): the tab bar stays the top level on every width; inside a tab, compact width
/// is a stack (list → pushed detail) and regular width a split view (list sidebar, independent detail). The selection is
/// one value, so collapsing to compact keeps the open chat pushed.
struct AdaptiveSplit<Item: Hashable, Sidebar: View, Detail: View, Placeholder: View>: View {
    @Binding var selection: Item?
    @ViewBuilder let sidebar: () -> Sidebar
    @ViewBuilder let detail: (Item) -> Detail
    @ViewBuilder let placeholder: () -> Placeholder
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?

    var body: some View {
        if sizeClass == .regular {
            NavigationSplitView {
                sidebar()
            } detail: {
                NavigationStack {
                    if let selection { detail(selection) } else { placeholder() }
                }
            }
            .navigationSplitViewStyle(.balanced)
        } else {
            NavigationStack {
                sidebar()
                    .navigationDestination(item: $selection) { item in detail(item) }
            }
        }
    }
}

/// A quick panel of a chat (plan §18): the glass capsule of the header and the floating capsule open these sheets.
enum ChatPanel: String, Identifiable, Sendable {
    case agents
    case background
    case files
    case screen
    /// The queue's sheet (owner's reference shots).
    case queue
    /// The run inspector.
    case inspector

    var id: String { rawValue }
}

// MARK: Agents

struct AgentsTab: View {
    var body: some View {
        NavigationStack {
            EmptyState(
                title: String(localized: "Agents come in a later version", comment: "Agents tab: empty state until the agents wave"), systemImage: "person.2",
                detail: String(localized: "Your agents and teams, their chats and schedules will show here.", comment: "Agents tab: empty state detail"),
                actionTitle: nil, action: {}
            )
            .background(Tone.ground)
            .navigationTitle(String(localized: "Agents", comment: "Title of the agents list"))
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: Activity

struct ActivityTab: View {
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    GroupedSection(header: nil, footer: String(localized: "Opens the Mac's live screen; nothing plays until you open it.", comment: "Activity tab: footer under Watch the Mac")) {
                        ListRow(systemImage: "desktopcomputer", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"), description: nil, accessory: .chevron)
                    }
                    EmptyState(
                        title: String(localized: "Nothing new since you were away", comment: "Activity tab empty state title"), systemImage: "clock.arrow.circlepath",
                        detail: String(localized: "What Dot does on its own (schedules, helpers, drafts that wait) shows here.", comment: "Activity tab empty state detail"),
                        actionTitle: nil, action: {})
                }
                .padding(Space.gutter)
                .readableColumn()
            }
            .background(Tone.ground)
            .navigationTitle(String(localized: "Activity", comment: "Title of the activity tab"))
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: Settings

struct SettingsTab: View {
    @Environment(AppModel.self) private var model: AppModel
    @AppStorage("toolCallMode") private var toolModeRaw: String = DotEngine.ToolCallMode.showAll.rawValue
    @AppStorage("responseStreaming") private var streaming: Bool = true
    @AppStorage("followUp") private var followUpRaw: String = FollowUp.queue.rawValue
    @State private var stopAsked: Bool = false
    @State private var stopProblem: String?
    @State private var stopDone: Bool = false
    @State private var forgetAsked: Bool = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.groupedGutter) {
                    Button(String(localized: "Emergency stop", comment: "Settings: stops every run of Dot at once"), systemImage: "stop.circle", role: .destructive) { stopAsked = true }
                        .buttonStyle(DestructiveButtonStyle())
                        .frame(maxWidth: .infinity)
                        .confirmationDialog(String(localized: "Stop everything Dot is doing?", comment: "Confirmation of the emergency stop"), isPresented: $stopAsked) {
                            Button(String(localized: "Stop everything", comment: "Confirmation button of the emergency stop"), role: .destructive) {
                                Task {
                                    stopProblem = await model.emergencyStop()
                                    stopDone = stopProblem == nil
                                }
                            }
                        } message: {
                            Text(String(localized: "Every run stops, desktop control is blocked and schedules wait until you release the stop on your Mac.", comment: "Emergency stop confirmation: what it does"))
                        }
                    if stopDone {
                        Notice(tone: .info, message: String(localized: "Everything stopped. Release the stop on your Mac to let Dot work again.", comment: "Settings: the emergency stop worked"), dismiss: { stopDone = false })
                    }
                    if let stopProblem {
                        Notice(tone: .failure, message: String(localized: "The stop did not reach your Mac: \(stopProblem)", comment: "Settings: the emergency stop failed; the argument is why"), dismiss: { self.stopProblem = nil })
                    }
                    GroupedSection(header: String(localized: "Connections", comment: "Settings section: the Mac, notifications, this phone"), footer: nil) {
                        ListRow(systemImage: "desktopcomputer", title: String(localized: "This Mac", comment: "Settings row: the paired Mac"), description: model.host?.name, accessory: .detail(transportWord))
                        ListRow(systemImage: "bell", title: String(localized: "Notifications", comment: "Settings row"), description: nil, accessory: .chevron)
                        Button { forgetAsked = true } label: {
                            ListRow(systemImage: "key", title: String(localized: "Forget this phone's key", comment: "Settings row: forgets the key this phone was paired with"), description: nil, accessory: .none)
                        }
                        .buttonStyle(RowPressStyle())
                        .confirmationDialog(String(localized: "Forget this phone's key? You pair again with a new code.", comment: "Confirmation of forgetting the phone's key"), isPresented: $forgetAsked, titleVisibility: .visible) {
                            Button(String(localized: "Forget the key", comment: "Confirmation button that forgets the phone's key"), role: .destructive) { Task { await model.forget() } }
                        }
                    }
                    GroupedSection(header: nil, footer: nil) {
                        ListRow(systemImage: "slider.horizontal.3", title: String(localized: "General", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "textformat.size", title: String(localized: "Appearance", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "bubble.left", title: String(localized: "Chat", comment: "Settings row: chat preferences"), description: nil, accessory: .chevron)
                    }
                    GroupedSection(
                        header: String(localized: "Tool calls in chat", comment: "Settings section: how much of the work log a chat shows"),
                        footer: String(localized: "Hide successful keeps failures. Zen shows only final answers. Approvals, questions and errors always show.", comment: "Footer of the tool calls setting")
                    ) {
                        ForEach(DotEngine.ToolCallMode.allCases, id: \.self) { mode in
                            RadioRow(title: mode.title, description: nil, isSelected: toolModeRaw == mode.rawValue) { toolModeRaw = mode.rawValue }
                        }
                        // The switch sits on the radio rows' inset (§19): no glyph pushing its title in.
                        ToggleRow(
                            systemImage: nil, title: String(localized: "Response streaming", comment: "Settings switch: answers appear word by word as written"),
                            description: String(localized: "On: words appear as Dot writes them. Off: an answer appears once it is whole.", comment: "Description of the response streaming switch"),
                            isOn: $streaming)
                    }
                    GroupedSection(
                        header: String(localized: "Follow-up while Dot works", comment: "Settings section: what a message written during a run does"),
                        footer: String(localized: "The other way is always on a long press of the button.", comment: "Footer of the follow-up setting")
                    ) {
                        RadioRow(
                            title: String(localized: "Queue", comment: "Follow-up setting: queued after the run"),
                            description: String(localized: "Starts after the run at work", comment: "Follow-up setting: what Queue does"), isSelected: followUpRaw == FollowUp.queue.rawValue
                        ) { followUpRaw = FollowUp.queue.rawValue }
                        RadioRow(
                            title: String(localized: "Steer", comment: "Follow-up setting: sent into the run"),
                            description: String(localized: "Goes into the running task at its next step", comment: "Follow-up setting: what Steer does"), isSelected: followUpRaw == FollowUp.steer.rawValue
                        ) { followUpRaw = FollowUp.steer.rawValue }
                    }
                    GroupedSection(header: String(localized: "Agents", comment: "Settings section: models, schedules, rules, memory"), footer: nil) {
                        ListRow(systemImage: "cpu", title: String(localized: "Models", comment: "Settings row"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "calendar.badge.clock", title: String(localized: "Scheduled tasks", comment: "Settings row"), description: nil, accessory: .detailAndChevron("7"))
                        ListRow(systemImage: "lock.shield", title: String(localized: "Rules", comment: "Settings row: what Dot may do without asking"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "brain", title: String(localized: "Memory", comment: "Settings row"), description: nil, accessory: .chevron)
                    }
                    GroupedSection(header: String(localized: "System", comment: "Settings section: health, version"), footer: nil) {
                        ListRow(systemImage: "heart.text.square", title: String(localized: "Health", comment: "Settings row: the daemon's health"), description: nil, accessory: .chevron)
                        ListRow(systemImage: "info.circle", title: String(localized: "About", comment: "Settings row"), description: nil, accessory: .detailAndChevron("0.1.0"))
                    }
                    #if DEBUG
                        GroupedSection(header: String(localized: "Design", comment: "Settings section of debug builds"), footer: String(localized: "Debug builds only.", comment: "Footer of the debug-only design section")) {
                            NavigationRow(systemImage: "square.grid.2x2", title: String(localized: "Gallery", comment: "Settings row of debug builds: the component gallery"), description: nil, detail: nil) {
                                GalleryIndex()
                            }
                        }
                    #endif
                }
                .padding(.horizontal, Space.gutter)
                .padding(.vertical, Space.groupedGutter)
                .readableColumn()
            }
            .background(Tone.ground)
            .navigationTitle(String(localized: "Settings", comment: "Title of the settings tab"))
            .background(Tone.ground)
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

extension SettingsTab {
    /// How the link to the Mac stands, in one word.
    var transportWord: String {
        switch model.snapshot.transport {
        case .up: String(localized: "Connected", comment: "Settings: the Mac answers")
        case .connecting: String(localized: "Connecting…", comment: "Settings: the link to the Mac is being opened")
        case .reconnecting: String(localized: "Reconnecting…", comment: "Chat subtitle: the link to the Mac broke and is being opened again")
        case .offline: String(localized: "Offline", comment: "Chat subtitle: the phone has no link")
        case .keyRefused: String(localized: "Key refused", comment: "Settings: the Mac no longer takes this phone's key")
        case .daemonTooOld: String(localized: "Update Dot on your Mac", comment: "Chat subtitle: the Mac runs a Dot too old for this app")
        }
    }
}
