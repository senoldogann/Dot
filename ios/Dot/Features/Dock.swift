import SwiftUI

/// The one capsule over the composer (item 1390): a single piece of glass whose segments are
/// divided by hairlines (the run's status, its helpers, the loops of the chat, the queue), each a button that opens its
/// detail in a sheet. It is one row and never stacks; with nothing to say it is not drawn. At the accessibility text sizes (item
/// 1642) the segments stand over each other with their full words, so that no word is cut to "You" and no time to "2m…".
struct DockCapsule: View {
    let summary: DockSummary
    let onOpen: (DockSummary.Kind) -> Void
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

    var body: some View {
        if !summary.isEmpty {
            Group {
                if typeSize.isAccessibilitySize {
                    stacked.dotGlass(in: RoundedRectangle(cornerRadius: Radius.xl + 4), tint: tint)
                } else {
                    // The long words when the width holds them; then the status in words with the rest as counts; then counts alone
                    // (a narrow phone).
                    ViewThatFits(in: .horizontal) {
                        row(.full)
                        row(.mixed)
                        row(.compact)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .dotGlass(in: Capsule(), tint: tint)
                }
            }
            .transition(.scale(scale: 0.92, anchor: .bottom).combined(with: .opacity))
        }
    }

    /// The one amber fill of the screen: the run waits on the owner.
    private var tint: Color? { summary.asksTheOwner ? Theme.warn.opacity(0.22) : nil }

    private enum Fit { case full, mixed, compact }

    private func row(_ fit: Fit) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(summary.segments.enumerated()), id: \.element.id) { index, segment in
                if index > 0 { Rectangle().fill(Theme.hairline).frame(width: 1, height: 16).accessibilityHidden(true) }
                button(segment, fit: fit)
            }
        }
    }

    /// The segments over each other, each with its whole words, divided by hairlines.
    private var stacked: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(summary.segments.enumerated()), id: \.element.id) { index, segment in
                if index > 0 { Rectangle().fill(Theme.hairline).frame(height: 1).accessibilityHidden(true) }
                button(segment, fit: .full).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func button(_ segment: DockSummary.Segment, fit: Fit) -> some View {
        Button { onOpen(segment.kind) } label: { label(segment, fit: fit) }
            .buttonStyle(DockSegmentStyle())
            .accessibilityIdentifier("dock-\(segment.kind.rawValue)")
            .accessibilityLabel(accessibilityLabel(segment))
            .accessibilityHint(hint(segment.kind))
    }

    private func label(_ segment: DockSummary.Segment, fit: Fit) -> some View {
        let short: Bool = fit == .compact || (fit == .mixed && segment.kind != .status)
        let accessible: Bool = typeSize.isAccessibilitySize
        // What is at work breathes: the working mark, the helpers waited for, a stream that reconnects, and the helpers at work in
        // the background (not while their count is only the last one read).
        let alive: Bool =
            (segment.kind == .status && [.audioWaveform, .circleDashed, .refresh].contains(segment.icon))
            || (segment.kind == .helpers && !segment.dimmed)
        return HStack(spacing: Space.s - 2) {
            DotIcon(segment.icon, size: 16, pulse: alive, relativeTo: .footnote)
                .foregroundStyle(tone(segment.tone))
                .frame(minWidth: 16)
            if let since: Double = segment.since {
                TimelineView(.periodic(from: .now, by: accessible ? 30 : 1)) { context in
                    Text(fit == .compact ? elapsed(since, now: context.date, seconds: !accessible) : "\(segment.words) · \(elapsed(since, now: context.date, seconds: !accessible))")
                        .font(Theme.meta.weight(.medium).monospacedDigit()).foregroundStyle(Theme.text)
                        .lineLimit(accessible ? 2 : 1)
                }
            } else {
                Text(short ? segment.short : segment.words)
                    .font(Theme.meta.weight(.medium).monospacedDigit()).foregroundStyle(Theme.text)
                    .lineLimit(accessible ? 2 : 1)
            }
            if fit == .full, let detail: String = segment.detail {
                Text(detail).font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(accessible ? 2 : 1)
                    .frame(maxWidth: accessible ? .infinity : 150, alignment: .leading)
            }
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, accessible ? Space.s : 0)
        .frame(minHeight: 44)
        .opacity(segment.dimmed ? 0.5 : 1)
        .contentShape(Rectangle())
    }

    /// Working is the ink itself (a neutral with its moving mark), waiting is amber, the rest is the secondary colour. No hue.
    private func tone(_ tone: DockSummary.Tone) -> Color {
        switch tone {
        case .plain: Theme.muted
        case .accent: Theme.text
        case .warn: Theme.warn
        }
    }

    private func accessibilityLabel(_ segment: DockSummary.Segment) -> String {
        [segment.words, segment.detail, segment.dimmed ? "last known" : nil].compactMap { $0 }.joined(separator: ", ")
    }

    private func hint(_ kind: DockSummary.Kind) -> String {
        switch kind {
        case .status: "Opens the run's details"
        case .helpers: "Opens the helpers of this chat"
        case .loops: "Opens the loops of this chat"
        case .queue: "Opens the queued messages to reorder, send now or remove"
        }
    }

    /// "2m 13s"; at the accessibility sizes the seconds are left out ("2m"), so that the time is never cut.
    private func elapsed(_ since: Double, now: Date, seconds showsSeconds: Bool) -> String {
        let seconds: Int = max(0, Int(now.timeIntervalSince1970 - since))
        if seconds < 60 { return showsSeconds ? "\(seconds)s" : "under 1m" }
        return showsSeconds ? "\(seconds / 60)m \(seconds % 60)s" : "\(seconds / 60)m"
    }
}

/// A segment's press: the words dim a little, so a tap in a capsule answers.
private struct DockSegmentStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.55 : 1)
    }
}

/// The queue, in a sheet (items 1390 and 1643): each message waiting to run is a card on the sheet's ground, numbered. A swipe to
/// the left removes it (with a few seconds to take it back), a swipe to the right sends it to the run at work now, a long press
/// offers the same with "Move up" and "Move down", and "Reorder" shows the handles to drag the order. A held queue says so and has a
/// Resume.
struct QueueSheet: View {
    let model: ChatModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var editMode: EditMode = .inactive
    /// The messages swiped away that the Mac has not been told of yet, and the one the "Removed · Undo" line is about.
    @State private var hidden: Set<String> = []
    @State private var removed: QueuedMessage?
    @State private var commit: Task<Void, Never>?

    /// How long a swipe can be taken back.
    private static let grace: Duration = .seconds(5)

    var body: some View {
        let shown: [QueuedMessage] = model.queue.messages.filter { !hidden.contains($0.id) }
        NavigationStack {
            List {
                if model.queue.held {
                    held.listRowBackground(Color.clear).listRowSeparator(.hidden).listRowInsets(EdgeInsets())
                }
                SectionLabel(title: "Next up")
                    .padding(.horizontal, Space.l).padding(.top, Space.xs)
                    .listRowBackground(Color.clear).listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets())
                    .moveDisabled(true)
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, message in
                    row(message, index: index, count: shown.count)
                }
                .onMove { offsets, destination in model.moveQueued(from: offsets, to: destination) }
                if shown.isEmpty {
                    Text("Nothing is queued.").font(Theme.small).foregroundStyle(Theme.muted)
                        .listRowBackground(Color.clear).listRowSeparator(.hidden).moveDisabled(true)
                }
                Text("The first one starts when this run finishes. Send now hands it over immediately.")
                    .font(Theme.meta).foregroundStyle(Theme.faint)
                    .padding(.horizontal, Space.l)
                    .listRowBackground(Color.clear).listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: Space.xs, leading: 0, bottom: Space.l, trailing: 0))
                    .moveDisabled(true)
            }
            .listStyle(.plain)
            .listRowSpacing(Space.s)
            .contentMargins(.horizontal, Space.l, for: .scrollContent)
            .scrollContentBackground(.hidden)
            .environment(\.editMode, $editMode)
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Queued")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if model.queue.messages.count > 1 {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(editMode == .active ? "Finish" : "Reorder") { toggleReordering() }
                            .accessibilityIdentifier("queue-reorder")
                    }
                }
                DoneButton { dismiss() }
            }
            .overlay(alignment: .bottom) { undoLine }
            .dotAnimation(Motion.snappy, value: shown.map(\.id))
            .sensoryFeedback(.selection, trigger: model.queue.messages.map(\.id))
            .onChange(of: model.queue.messages.isEmpty) { if model.queue.messages.isEmpty { dismiss() } }
            .onDisappear { commitNow() }
        }
    }

    private var held: some View {
        HStack(alignment: .center, spacing: Space.m) {
            DotIcon(.circlePause, size: 22).foregroundStyle(Theme.warn)
            Text("The queue is paused: a stop or a failed run held it.")
                .font(Theme.small).foregroundStyle(Theme.text)
            Spacer(minLength: Space.s)
            Button("Resume") { model.resumeQueue() }
                .dotButton(.primary).controlSize(.small)
                .accessibilityIdentifier("queue-resume")
        }
        .padding(Space.l)
        .card(border: Theme.warn.opacity(0.45))
    }

    private func row(_ message: QueuedMessage, index: Int, count: Int) -> some View {
        HStack(alignment: .top, spacing: Space.m) {
            Text("\(index + 1)").font(Theme.meta.weight(.semibold).monospacedDigit()).foregroundStyle(Theme.faint)
                .frame(width: 18, alignment: .trailing).padding(.top, 2)
            Text(message.text).font(Theme.small).foregroundStyle(Theme.text).lineLimit(5)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(Space.l)
        .frame(minHeight: 56)
        .listRowBackground(
            RoundedRectangle(cornerRadius: Radius.l).fill(Theme.surface)
                .overlay(RoundedRectangle(cornerRadius: Radius.l).strokeBorder(Theme.hairline, lineWidth: 1))
        )
        .listRowSeparator(.hidden)
        .listRowInsets(EdgeInsets())
        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
            Button { remove(message) } label: { Label("Remove", menuIcon: .trash) }
                .tint(Theme.swipeDestructive)
                .accessibilityIdentifier("queue-remove")
        }
        .swipeActions(edge: .leading, allowsFullSwipe: false) {
            if model.isWorking {
                Button { model.steerQueued(message.id) } label: { Label("Send now", menuIcon: .cornerUpRight) }
                    .tint(Theme.swipeNeutral)
                    .accessibilityIdentifier("queue-send-now")
            }
        }
        .contextMenu {
            if model.isWorking {
                Button { model.steerQueued(message.id) } label: { Label("Send now", menuIcon: .cornerUpRight) }
            }
            if index > 0 { Button { model.moveQueued(message.id, by: -1) } label: { Label("Move up", menuIcon: .arrowUp) } }
            if index < count - 1 { Button { model.moveQueued(message.id, by: 1) } label: { Label("Move down", menuIcon: .arrowDown) } }
            Button(role: .destructive) { remove(message) } label: { Label("Remove", menuIcon: .trash) }
        }
        .accessibilityAction(named: "Remove") { remove(message) }
        .accessibilityAction(named: "Move up") { model.moveQueued(message.id, by: -1) }
        .accessibilityAction(named: "Move down") { model.moveQueued(message.id, by: 1) }
    }

    // MARK: A removal that can be taken back

    /// Hides the message at once; the Mac is told when the few seconds to take it back have passed (or the sheet goes).
    private func remove(_ message: QueuedMessage) {
        commitNow()
        hidden.insert(message.id)
        removed = message
        commit = Task {
            try? await Task.sleep(for: Self.grace)
            if !Task.isCancelled { commitNow() }
        }
    }

    private func commitNow() {
        commit?.cancel()
        commit = nil
        if let message: QueuedMessage = removed { model.removeQueued(message.id) }
        removed = nil
        hidden = []
    }

    private func undo() {
        commit?.cancel()
        commit = nil
        if let message: QueuedMessage = removed { hidden.remove(message.id) }
        removed = nil
    }

    private func toggleReordering() {
        commitNow()
        editMode = editMode == .active ? .inactive : .active
    }

    @ViewBuilder private var undoLine: some View {
        if removed != nil {
            HStack(spacing: Space.m) {
                Text("Removed from the queue").font(Theme.small).foregroundStyle(Theme.text)
                Button("Undo") { undo() }
                    .font(Theme.small.weight(.semibold)).foregroundStyle(Theme.text).underline()
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("queue-undo")
            }
            .padding(.horizontal, Space.l)
            .dotGlass(in: Capsule())
            .padding(.bottom, Space.l)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}
