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
    /// The model of this chat (`/model`).
    case model
    /// The plan of the chat's newest run (item 1880).
    case plan

    var id: String { rawValue }
}

// MARK: Agents

struct AgentsTab: View {
    @State private var opensWeb: Bool = false

    var body: some View {
        NavigationStack {
            EmptyState(
                title: String(localized: "Agents come in a later version", comment: "Agents tab: empty state until the agents wave"), systemImage: "person.2",
                detail: String(localized: "Until then your agents and teams open from your Mac's own page.", comment: "Agents tab: empty state detail with the web page"),
                actionTitle: String(localized: "Open agents and teams", comment: "Agents tab: opens the web page of agents"), action: { opensWeb = true }
            )
            .navigationDestination(isPresented: $opensWeb) {
                WebSettingsPage(target: WebSettingsPage.Target(tab: "/agents", title: String(localized: "Agents and teams", comment: "Settings row: the web page of agents, teams and handoffs")))
            }
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
                        NavigationLink {
                            WebSettingsPage(target: WebSettingsPage.Target(tab: "/watch", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen")))
                        } label: {
                            ListRow(systemImage: "desktopcomputer", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"), description: nil, accessory: .web)
                        }
                        .buttonStyle(RowPressStyle())
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
