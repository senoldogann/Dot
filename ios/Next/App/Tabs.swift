import DotEngine
import SwiftUI
import UIKit

/// The iPad decision (review S9, PLAN §27): compact width is a stack (the home → a pushed bot's chat) and regular width a
/// split view (the home as the sidebar, the chat as the detail). The selection is one value, so collapsing to compact
/// keeps the open chat pushed.
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

    var id: String { rawValue }
}
