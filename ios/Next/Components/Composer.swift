import SwiftUI

/// The composer's two shapes (review S4): compact at rest (one row), split into the field and a toolbar once the
/// owner writes more than a line or focuses it.
enum ComposerLayout: Equatable, Sendable {
    case compact
    case expanded

    /// Expanded while focused or while the text is longer than one short line.
    static func of(text: String, isFocused: Bool) -> ComposerLayout {
        if isFocused || text.contains("\n") || text.count > 32 { return .expanded }
        return .compact
    }
}

/// The send button: an ink disc of 30 pt inside a 44 pt target; empty text dims it.
struct SendButton: View {
    let isEnabled: Bool
    let send: () -> Void
    /// The other way to send (D10: long-press on Send): into the running task while one works; `nil` for none.
    var alternate: (() -> Void)? = nil

    var body: some View {
        Button(action: send) {
            Image(systemName: "arrow.up")
                .font(.system(.callout, weight: .semibold))
                .foregroundStyle(isEnabled ? Tone.onAction : Tone.tertiary)
                .frame(width: Metric.sendDisc, height: Metric.sendDisc)
                .background(isEnabled ? Tone.action : Tone.nested, in: Circle())
                .frame(width: Metric.hit, height: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .contextMenu {
            if let alternate {
                Button(String(localized: "Send now into the running task", comment: "Composer long-press: steer instead of queue"), systemImage: "arrow.turn.down.right", action: alternate)
            }
        }
        .accessibilityLabel(String(localized: "Send", comment: "VoiceOver: the composer's send button"))
        .accessibilityActions {
            if let alternate {
                Button(String(localized: "Send now into the running task", comment: "Composer long-press: steer instead of queue"), action: alternate)
            }
        }
    }
}

/// A round 44 pt toolbar button of the composer.
struct ComposerIconButton: View {
    let systemImage: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Symbol(name: systemImage, context: .toolbar)
                .foregroundStyle(Tone.secondary)
                .frame(width: Metric.hit, height: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// The composer's glass shell (plan §4.1, R-a §3): radius 27, compact at rest with attach, the field and Send on one
/// row; expanded with the field above and attach, the model chip, the microphone and Send beneath it. The real
/// behaviour (drafts, queue/steer, attachments) comes in wave N2.
struct ComposerShell: View {
    @Binding var text: String
    let placeholder: String
    let modelTitle: String
    let send: () -> Void
    /// Long-press on Send: the other way to send (steer while a run works); `nil` for none.
    var steer: (() -> Void)? = nil
    @FocusState private var isFocused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    var body: some View {
        let layout: ComposerLayout = ComposerLayout.of(text: text, isFocused: isFocused)
        // One field in one place whatever the layout: a field made anew when the composer grows on focus would take the
        // focus away again, and the keyboard would never stay (found in the N1 end-to-end check).
        let compact: Bool = layout == .compact
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .bottom, spacing: 0) {
                if compact {
                    ComposerIconButton(systemImage: "plus", label: String(localized: "Attach", comment: "VoiceOver: the composer's button that adds a photo or file"), action: {})
                }
                field(lines: compact ? 1...4 : 1...6)
                    .padding(.vertical, compact ? Space.s : 0)
                    .padding(.horizontal, compact ? 0 : Space.m - Space.xxs)
                    .padding(.top, compact ? 0 : Space.m)
                if compact {
                    ComposerIconButton(systemImage: "mic", label: String(localized: "Dictate", comment: "VoiceOver: the composer's microphone button"), action: {})
                    SendButton(isEnabled: canSend, send: send, alternate: steer)
                }
            }
            if !compact {
                HStack(spacing: 0) {
                    ComposerIconButton(systemImage: "plus", label: String(localized: "Attach", comment: "VoiceOver: the composer's button that adds a photo or file"), action: {})
                    Menu {
                        Button(String(localized: "Default model", comment: "Composer model menu: use the daemon's default model"), action: {})
                    } label: {
                        HStack(spacing: Space.xxs) {
                            Text(modelTitle).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
                            Symbol(name: "chevron.down", context: .status).foregroundStyle(Tone.glyph)
                        }
                        .padding(.horizontal, Space.xs)
                        .frame(minHeight: Metric.hit)
                        .contentShape(Rectangle())
                    }
                    .accessibilityLabel(String(localized: "Model, \(modelTitle)", comment: "VoiceOver: the composer's model menu; the argument is the model and effort"))
                    Spacer(minLength: 0)
                    ComposerIconButton(systemImage: "mic", label: String(localized: "Dictate", comment: "VoiceOver: the composer's microphone button"), action: {})
                    SendButton(isEnabled: canSend, send: send, alternate: steer)
                }
            }
        }
        .padding(.horizontal, Space.xxs)
        .interactiveGlass(in: rounded(Radius.composer))
        .animation(motionAnimation(.message, reduceMotion: reduceMotion), value: layout)
    }

    private var canSend: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    private func field(lines: ClosedRange<Int>) -> some View {
        TextField(placeholder, text: $text, axis: .vertical)
            .textRole(.body)
            .foregroundStyle(Tone.text)
            .tint(Tone.text)
            .lineLimit(lines)
            .focused($isFocused)
    }
}

/// What the floating capsule says about the chat's work; a segment exists only while it has something to say.
struct WorkingSummary: Equatable, Sendable {
    let status: ChatStatus?
    let elapsed: String?
    let queued: Int
    let helpers: Int

    var isEmpty: Bool { status == nil && queued == 0 && helpers == 0 }
}

/// The floating working capsule and the scroll-to-bottom circle in one glass container, so the two merge
/// (plan §4.1, R-a §3). Each segment is its own 44 pt button; at the accessibility sizes the segments stack.
struct FloatingWorkingControl: View {
    let summary: WorkingSummary
    let showsScrollToBottom: Bool
    let openStatus: () -> Void
    let openQueue: () -> Void
    let openHelpers: () -> Void
    let scrollToBottom: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        GlassEffectContainer(spacing: 12) {
            HStack(spacing: Space.xs) {
                if !summary.isEmpty { capsule }
                if showsScrollToBottom {
                    Button(action: scrollToBottom) {
                        Symbol(name: "chevron.down", context: .toolbar)
                            .foregroundStyle(Tone.text)
                            .frame(width: Metric.hit, height: Metric.hit)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .interactiveGlass(in: Circle())
                    .accessibilityLabel(String(localized: "Scroll to the newest message", comment: "VoiceOver: the round button over the composer"))
                }
            }
        }
    }

    private var capsule: some View {
        let layout: AnyLayout =
            dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 0))
            : AnyLayout(HStackLayout(spacing: 0))
        return layout {
            if let status = summary.status {
                segment(action: openStatus, label: [status.word, summary.elapsed].compactMap { $0 }.joined(separator: ", ")) {
                    HStack(spacing: Space.xxs + 1) {
                        StatusWord(status: status)
                        if let elapsed = summary.elapsed {
                            Text(elapsed).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers()
                        }
                    }
                }
            }
            if summary.queued > 0 {
                divider
                segment(action: openQueue, label: String(localized: "\(summary.queued) queued", comment: "VoiceOver: the capsule segment of queued messages; the argument is their count")) {
                    Label { Text(verbatim: "\(summary.queued)") } icon: { Image(systemName: "list.number") }.textRole(.label).fontWeight(.medium)
                        .foregroundStyle(Tone.secondary)
                }
            }
            if summary.helpers > 0 {
                divider
                segment(action: openHelpers, label: String(localized: "\(summary.helpers) helpers working", comment: "VoiceOver: the capsule segment of helpers at work; the argument is their count")) {
                    Label { Text(verbatim: "\(summary.helpers)") } icon: { Image(systemName: "person.2") }.textRole(.label).fontWeight(.medium)
                        .foregroundStyle(Tone.secondary)
                }
            }
        }
        .padding(.horizontal, Space.xxs)
        .interactiveGlass(in: dynamicTypeSize.isAccessibilitySize ? AnyShape(rounded(Radius.bubble)) : AnyShape(Capsule()))
    }

    @ViewBuilder private var divider: some View {
        if !dynamicTypeSize.isAccessibilitySize {
            Rectangle().fill(Tone.separator).frame(width: 1, height: 14).accessibilityHidden(true)
        }
    }

    private func segment<Label: View>(action: @escaping () -> Void, label: String, @ViewBuilder content: () -> Label)
        -> some View
    {
        Button(action: action) {
            content()
                .padding(.horizontal, Space.s)
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
    }
}
