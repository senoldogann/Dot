import SwiftUI

/// The one capsule over the composer (item 1390): a single piece of glass whose segments are
/// divided by hairlines (the run's status, its helpers, the loops of the chat, the queue), each a button that opens its
/// detail in a sheet. It is one row and never stacks; with nothing to say it is not drawn.
struct DockCapsule: View {
    let summary: DockSummary
    let onOpen: (DockSummary.Kind) -> Void

    var body: some View {
        if !summary.isEmpty {
            // The long words when the width holds them; then the status in words with the rest as counts; then counts alone
            // (a narrow phone, a large text size).
            ViewThatFits(in: .horizontal) {
                row(.full)
                row(.mixed)
                row(.compact)
            }
            .fixedSize(horizontal: false, vertical: true)
            .dotGlass(in: Capsule(), tint: summary.asksTheOwner ? Theme.warn.opacity(0.22) : nil)
            .transition(.scale(scale: 0.92, anchor: .bottom).combined(with: .opacity))
        }
    }

    private enum Fit { case full, mixed, compact }

    private func row(_ fit: Fit) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(summary.segments.enumerated()), id: \.element.id) { index, segment in
                if index > 0 { Rectangle().fill(Theme.hairline).frame(width: 1, height: 16).accessibilityHidden(true) }
                Button { onOpen(segment.kind) } label: { label(segment, fit: fit) }
                    .buttonStyle(DockSegmentStyle())
                    .accessibilityIdentifier("dock-\(segment.kind.rawValue)")
                    .accessibilityLabel(accessibilityLabel(segment))
                    .accessibilityHint(hint(segment.kind))
            }
        }
    }

    private func label(_ segment: DockSummary.Segment, fit: Fit) -> some View {
        let short: Bool = fit == .compact || (fit == .mixed && segment.kind != .status)
        // What is at work breathes: the working mark, the helpers waited for, a stream that reconnects.
        let alive: Bool = segment.kind == .status && [.audioWaveform, .circleDashed, .refresh].contains(segment.icon)
        return HStack(spacing: Space.s - 2) {
            DotIcon(segment.icon, size: 16, pulse: alive, relativeTo: .footnote)
                .foregroundStyle(tone(segment.tone))
                .frame(minWidth: 16)
            if let since: Double = segment.since {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(fit == .compact ? elapsed(since, now: context.date) : "\(segment.words) · \(elapsed(since, now: context.date))")
                        .font(Theme.meta.weight(.medium).monospacedDigit()).foregroundStyle(Theme.text).lineLimit(1)
                }
            } else {
                Text(short ? segment.short : segment.words)
                    .font(Theme.meta.weight(.medium).monospacedDigit()).foregroundStyle(Theme.text).lineLimit(1)
            }
            if fit == .full, let detail: String = segment.detail {
                Text(detail).font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(1).frame(maxWidth: 150, alignment: .leading)
            }
        }
        .padding(.horizontal, Space.m)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
    }

    private func tone(_ tone: DockSummary.Tone) -> Color {
        switch tone {
        case .plain: Theme.muted
        case .accent: Theme.accent
        case .warn: Theme.warn
        }
    }

    private func accessibilityLabel(_ segment: DockSummary.Segment) -> String {
        [segment.words, segment.detail].compactMap { $0 }.joined(separator: ", ")
    }

    private func hint(_ kind: DockSummary.Kind) -> String {
        switch kind {
        case .status: "Opens the run's details"
        case .helpers: "Opens the helpers of this chat"
        case .loops: "Opens the loops of this chat"
        case .queue: "Opens the queued messages to move, send now or remove"
        }
    }

    private func elapsed(_ since: Double, now: Date) -> String {
        let seconds: Int = max(0, Int(now.timeIntervalSince1970 - since))
        return seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
    }
}

/// A segment's press: the words dim a little, so a tap in a capsule answers.
private struct DockSegmentStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.55 : 1)
    }
}

/// The queue, in a sheet (item 1390): each message waiting to run, numbered, with an up and a down arrow to move it, a
/// button to send it to the run at work now, and one to remove it. A held queue says so and has a Resume. There is no
/// drag: a place changes by its arrows (item 1348).
struct QueueSheet: View {
    let model: ChatModel
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    if model.queue.held { held }
                    CardGroup(
                        title: "Next up",
                        footer: "The first one starts when this run finishes. Send now hands it over immediately."
                    ) {
                        let messages: [QueuedMessage] = model.queue.messages
                        ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                            if index > 0 { CardDivider(inset: Space.l) }
                            row(message, index: index, count: messages.count)
                        }
                    }
                }
                .padding(Space.l)
            }
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Queued")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .dotAnimation(Motion.snappy, value: model.queue.messages.map(\.id))
            .sensoryFeedback(.selection, trigger: model.queue.messages.map(\.id))
            .onChange(of: model.queue.messages.isEmpty) { if model.queue.messages.isEmpty { dismiss() } }
        }
    }

    private var held: some View {
        HStack(alignment: .center, spacing: Space.m) {
            DotIcon(.circlePause, size: 22).foregroundStyle(Theme.warn)
            Text("The queue is paused: a stop or a failed run held it.")
                .font(Theme.small).foregroundStyle(Theme.text)
            Spacer(minLength: Space.s)
            Button("Resume") { model.resumeQueue() }
                .dotButton(.prominent).controlSize(.small)
                .accessibilityIdentifier("queue-resume")
        }
        .padding(Space.l)
        .card()
    }

    private func row(_ message: QueuedMessage, index: Int, count: Int) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(alignment: .top, spacing: Space.m) {
                Text("\(index + 1)").font(Theme.meta.weight(.semibold).monospacedDigit()).foregroundStyle(Theme.faint)
                    .frame(width: 18, alignment: .trailing).padding(.top, 2)
                Text(message.text).font(Theme.small).foregroundStyle(Theme.text).lineLimit(5)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 0) {
                Button { model.steerQueued(message.id) } label: {
                    Label("Send now", icon: .cornerUpRight, size: 14).font(Theme.meta.weight(.semibold))
                        .foregroundStyle(model.isWorking ? Theme.accent : Theme.faint)
                        .padding(.horizontal, Space.m).frame(minHeight: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!model.isWorking)
                .accessibilityIdentifier("queue-send-now")
                Spacer(minLength: 0)
                arrow(.chevronUp, label: "Move up", enabled: index > 0) { model.moveQueued(message.id, by: -1) }
                arrow(.chevronDown, label: "Move down", enabled: index < count - 1) { model.moveQueued(message.id, by: 1) }
                arrow(.trash, label: "Remove", enabled: true) { model.removeQueued(message.id) }
            }
            .padding(.leading, 18 + Space.m)
        }
        .padding(.horizontal, Space.l).padding(.top, Space.m).padding(.bottom, Space.xs)
    }

    private func arrow(_ icon: IconName, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            DotIcon(icon, size: 17)
                .foregroundStyle(enabled ? (icon == .trash ? Theme.bad : Theme.muted) : Theme.faint.opacity(0.45))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}
