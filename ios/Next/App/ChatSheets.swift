import DotEngine
import SwiftUI

/// The chat's queue (owner's reference shots `ref-queued-sheet.png`, `ref-editing-queued.png`, §20): each queued message
/// with a "Send now" ink pill; a tap edits it in the composer; a swipe removes it with Undo; a drag (or the VoiceOver
/// actions Move up / Move down) reorders; a held queue offers "Resume queue".
struct QueueSheet: View {
    let thread: String
    let edit: (QueuedMessageView) -> Void
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var removing: Set<String> = []
    @State private var toast: ToastMessage?
    @State private var removal: [UUID: String] = [:]

    private var queue: QueueView? { model.chats[thread]?.view?.queue }
    private var messages: [QueuedMessageView] { (queue?.messages ?? []).filter { !removing.contains($0.id) } }

    var body: some View {
        Group {
            if messages.isEmpty {
                EmptyState(
                    title: String(localized: "Nothing queued", comment: "Queue sheet empty state"), systemImage: "list.number",
                    detail: String(localized: "A message written while Dot works waits here and starts after the run.", comment: "Queue sheet empty state detail"),
                    actionTitle: nil, action: {})
            } else {
                List {
                    if queue?.held == true {
                        HStack(spacing: Space.s) {
                            Text(String(localized: "The queue is held: nothing starts until you resume it.", comment: "Queue sheet: the queue is held"))
                                .textRole(.footnote).foregroundStyle(Tone.secondary)
                            Spacer(minLength: 0)
                            Button(String(localized: "Resume queue", comment: "Queue sheet: lets a held queue go on")) { model.perform(.resumeQueue(thread: thread)) }
                                .buttonStyle(SecondaryButtonStyle())
                        }
                        .listRowBackground(Color.clear)
                    }
                    ForEach(messages, id: \.id) { message in
                        row(message)
                            .listRowBackground(Color.clear)
                            .listRowSeparatorTint(Tone.separator)
                            .alignmentGuide(.listRowSeparatorLeading) { _ in 0 }
                            .listRowInsets(EdgeInsets(top: Space.xs, leading: Space.l, bottom: Space.xs, trailing: Space.l))
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button(String(localized: "Remove", comment: "Queue sheet: removes a queued message"), systemImage: "trash", role: .destructive) { remove(message) }
                                    .tint(Tone.failure)
                            }
                    }
                    .onMove { from, to in
                        var ids: [String] = messages.map(\.id)
                        ids.move(fromOffsets: from, toOffset: to)
                        model.perform(.reorder(thread: thread, ids: ids))
                    }
                }
                .listStyle(.plain)
            }
        }
        .toastHost($toast, seconds: 4) { message in
            // Undo: the message stays.
            if let id = removal[message.id] { removing.remove(id) }
            removal[message.id] = nil
        }
        .onChange(of: toast) { old, new in
            // The toast went without Undo: the removal is sent.
            guard let old, old != new, let id = removal[old.id] else { return }
            removal[old.id] = nil
            if removing.contains(id) { model.perform(.removeQueued(thread: thread, message: id)) }
        }
        .inspectorSheet(title: String(localized: "Queued", comment: "Title of the queue sheet"), close: close)
    }

    private func row(_ message: QueuedMessageView) -> some View {
        let ids: [String] = messages.map(\.id)
        let at: Int = ids.firstIndex(of: message.id) ?? 0
        return HStack(spacing: Space.s) {
            Button { edit(message) } label: {
                Text(message.text).textRole(.body).foregroundStyle(Tone.text).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                    .frame(minHeight: Metric.hit).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("queue.row")
            .accessibilityHint(String(localized: "Edits it in the composer", comment: "VoiceOver hint of a queued message"))
            Button { model.perform(.steerQueued(thread: thread, message: message.id)) } label: {
                Label(String(localized: "Send now", comment: "Queue sheet: sends a queued message into the running task now"), systemImage: "arrow.up")
                    .textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.onAction)
                    .padding(.horizontal, Space.m).frame(minHeight: 34).background(Tone.action, in: Capsule())
                    .frame(minHeight: Metric.hit).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(String(localized: "Queued \(at + 1) of \(ids.count): \(message.text)", comment: "VoiceOver: a queued message; the arguments are its place, the count and its words"))
        .accessibilityActions {
            Button(String(localized: "Send now", comment: "Queue sheet: sends a queued message into the running task now")) { model.perform(.steerQueued(thread: thread, message: message.id)) }
            Button(String(localized: "Edit", comment: "Under a refused message: puts its words back in the composer")) { edit(message) }
            if at > 0 {
                Button(String(localized: "Move up", comment: "VoiceOver action: moves a queued message one place earlier")) {
                    model.perform(.reorder(thread: thread, ids: reordered(ids, moving: message.id, to: at - 1)))
                }
            }
            if at < ids.count - 1 {
                Button(String(localized: "Move down", comment: "VoiceOver action: moves a queued message one place later")) {
                    model.perform(.reorder(thread: thread, ids: reordered(ids, moving: message.id, to: at + 1)))
                }
            }
            Button(String(localized: "Remove", comment: "Queue sheet: removes a queued message")) { remove(message) }
        }
    }

    private func remove(_ message: QueuedMessageView) {
        removing.insert(message.id)
        let shown: ToastMessage = ToastMessage(
            id: UUID(), text: String(localized: "Removed from the queue", comment: "Toast after removing a queued message"), systemImage: "trash",
            actionTitle: String(localized: "Undo", comment: "Toast action: takes the last change back"))
        removal[shown.id] = message.id
        toast = shown
    }
}

/// One line of the inspector: a glyph, the title, the value at the trailing edge (a chevron where it opens something).
struct InspectorRow: View {
    let title: String
    let systemImage: String
    let accessory: RowAccessory
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let layout: AnyLayout = dynamicTypeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.xxs)) : AnyLayout(HStackLayout(spacing: Space.s))
        layout {
            Label { Text(title).textRole(.body).foregroundStyle(Tone.text) } icon: { Symbol(name: systemImage, context: .row).foregroundStyle(Tone.secondary) }
            if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: Space.xs) }
            switch accessory {
            case .detail(let text), .detailAndChevron(let text):
                Text(text).textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
            case .none, .chevron, .web:
                EmptyView()
            }
            if accessory == .chevron || { if case .detailAndChevron = accessory { true } else { false } }() {
                Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
            }
        }
        .frame(minHeight: Metric.hit)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
