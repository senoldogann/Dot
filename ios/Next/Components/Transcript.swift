import SwiftUI
import UIKit

/// Lays its one child out at most `fraction` of the offered width, against the trailing edge: the owner's bubble.
struct TrailingFractionLayout: Layout {
    let fraction: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        let width: CGFloat = proposal.width ?? 320
        let size: CGSize = child.sizeThatFits(ProposedViewSize(width: width * fraction, height: nil))
        return CGSize(width: width, height: size.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let child = subviews.first else { return }
        let size: CGSize = child.sizeThatFits(ProposedViewSize(width: bounds.width * fraction, height: nil))
        child.place(
            at: CGPoint(x: bounds.maxX - size.width, y: bounds.minY), anchor: .topLeading,
            proposal: ProposedViewSize(width: size.width, height: size.height))
    }
}

/// The owner's message (plan §4.1): a bubble at most 85 % wide, radius 20, the nested fill, no stroke.
struct UserBubble: View {
    let text: String
    /// The lines shown while a long message is folded; `nil` shows it whole.
    var lineLimit: Int? = nil

    var body: some View {
        TrailingFractionLayout(fraction: Metric.bubbleFraction) {
            Text(text)
                .lineLimit(lineLimit)
                .textRole(.body)
                .foregroundStyle(Tone.text)
                // No selection on a hold: the hold opens the message's actions, Select text among them (web
                // `messagesheet.js`: a touch screen's hold is the sheet's alone).
                .padding(.horizontal, Space.m - 1.75)
                .padding(.vertical, Space.s - 1.75)
                .background(Tone.nested, in: rounded(Radius.bubble))
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(String(localized: "You: \(text)", comment: "VoiceOver: the owner's own message; the argument is its text"))
    }
}

/// Inline markdown of an answer, with links in the ink and underlined (review S3: a link is told by its underline,
/// never by a hue). Throws when the markdown does not parse.
func answerText(markdown: String) throws -> AttributedString {
    var result: AttributedString = try AttributedString(
        markdown: markdown, options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace))
    for run in result.runs where run.link != nil {
        result[run.range].underlineStyle = .single
    }
    return result
}

/// Dot's answer (plan §4.1): no bubble, full width plain text in the body role, selectable.
struct AnswerText: View {
    let text: AttributedString

    var body: some View {
        Text(text)
            .textRole(.body)
            .foregroundStyle(Tone.text)
            .tint(Tone.text)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A code block (plan §4.1): a header row with the language and Copy, the code in the monospaced role scrolling
/// sideways; the nested fill, radius 12, no stroke.
struct CodeBlock: View {
    let language: String
    let code: String
    @AppStorage(PrefKey.codeFontSize) private var codeSize: Double = 0
    @AppStorage(PrefKey.codeWordBreak) private var wordBreak: Bool = true
    @State private var copied: Int = 0
    @State private var showsCopied: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language).textRole(.caption).fontWeight(.medium).foregroundStyle(Tone.secondary)
                Spacer()
                Button {
                    UIPasteboard.general.string = code
                    copied += 1
                    showsCopied = true
                } label: {
                    Label(showsCopied ? String(localized: "Copied", comment: "Code block header after a copy") : String(localized: "Copy", comment: "Code block header button that copies the code"), systemImage: showsCopied ? "checkmark" : "doc.on.doc")
                        .textRole(.caption).fontWeight(.medium)
                        .foregroundStyle(Tone.secondary)
                        .frame(minWidth: Metric.hit, minHeight: Metric.hit)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .haptic(.light, trigger: copied)
                .accessibilityLabel(showsCopied ? String(localized: "Copied", comment: "Code block header after a copy") : String(localized: "Copy code", comment: "VoiceOver: the code block's copy button"))
            }
            .padding(.leading, Space.m)
            .padding(.trailing, Space.xxs)
            .frame(minHeight: 32)
            if wordBreak {
                codeText.fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ScrollView(.horizontal) { codeText.fixedSize(horizontal: true, vertical: false) }.scrollIndicators(.hidden)
            }
        }
        .background(Tone.nested, in: rounded(Radius.inner))
        .task(id: copied) {
            guard copied > 0 else { return }
            do {
                try await Task.sleep(for: .seconds(1.6))
            } catch is CancellationError {
                return
            } catch {
                preconditionFailure("Task.sleep throws only CancellationError: \(error)")
            }
            showsCopied = false
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(String(localized: "Code, \(language)", comment: "VoiceOver: a code block; the argument is its language"))
    }

    /// The code in the monospaced role, or in the size chosen in Settings › Appearance (item 1769).
    private var codeText: some View {
        Text(code)
            .textRole(.code)
            .modifier(MonoSize(points: codeSize))
            .foregroundStyle(Tone.text)
            .textSelection(.enabled)
            .padding(.horizontal, Space.m)
            .padding(.bottom, Space.s)
    }
}

/// A monospaced size chosen in Settings › Appearance over the code role's own; 0 keeps the role's, which follows the
/// text size (item 1769).
struct MonoSize: ViewModifier {
    let points: Double
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    /// The chosen size at the default text size, scaled with the text size as the code role is.
    func body(content: Content) -> some View {
        if points > 0 {
            let traits: UITraitCollection = UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(dynamicTypeSize))
            content.font(.system(size: UIFontMetrics(forTextStyle: .footnote).scaledValue(for: points, compatibleWith: traits), design: .monospaced))
        } else {
            content
        }
    }
}

/// How a tool call stands.
enum ToolState: Equatable, Sendable {
    case running
    case done
    case failed
}

/// One tool call of the work log.
struct ToolCall: Identifiable, Equatable, Sendable {
    let id: String
    let symbol: String
    let label: String
    let state: ToolState
    let detail: String?
}

/// A sweep of light across running work (R-a §3): 1.35 s across, 1.45 s still; none under Reduce Motion.
struct Shimmer: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    static let sweep: Double = 1.35
    static let pause: Double = 1.45
    static let band: CGFloat = 72

    func body(content: Content) -> some View {
        if reduceMotion {
            content.foregroundStyle(Tone.secondary)
        } else {
            content
                .foregroundStyle(Tone.tertiary)
                .overlay {
                    // At most 30 drawings a second: the band moves smoothly and the phone does half the work (item 1932).
                    TimelineView(.animation(minimumInterval: 1.0 / 30)) { timeline in
                        GeometryReader { proxy in
                            let phase: Double = shimmerPhase(
                                at: timeline.date.timeIntervalSinceReferenceDate, sweep: Self.sweep, pause: Self.pause)
                            content
                                .foregroundStyle(Tone.text)
                                .mask(alignment: .leading) {
                                    LinearGradient(
                                        colors: [.clear, .white, .clear], startPoint: .leading, endPoint: .trailing
                                    )
                                    .frame(width: Self.band)
                                    .offset(x: -Self.band + phase * (proxy.size.width + Self.band))
                                }
                        }
                    }
                }
        }
    }
}

/// Where the shimmer's band is, 0 (before the start) to 1 (past the end), for a time in seconds.
func shimmerPhase(at time: Double, sweep: Double, pause: Double) -> Double {
    let position: Double = time.truncatingRemainder(dividingBy: sweep + pause)
    return min(1, position / sweep)
}

/// A tool call as one quiet line (plan §4.1, R-a §3): a 28 pt visual pitch, a 13 pt glyph, the label in secondary ink,
/// a shimmer while it runs, a red ✕ when it failed (VoiceOver says "failed"); a tap opens its detail (monospaced,
/// scrolling past 210 pt), a long press copies it.
struct ToolCallRow: View {
    let call: ToolCall
    @Binding var isExpanded: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                isExpanded.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    line
                    Spacer(minLength: Space.xs)
                    if call.state == .failed {
                        Symbol(name: "xmark", context: .status).foregroundStyle(Tone.failure)
                    }
                    if call.detail != nil {
                        Symbol(name: "chevron.down", context: .status)
                            .foregroundStyle(Tone.glyph)
                            .rotationEffect(.degrees(isExpanded ? 180 : 0))
                    }
                }
                .frame(minHeight: Metric.toolRow)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(call.detail == nil)
            .haptic(.selection, trigger: isExpanded)
            .contextMenu {
                Button(String(localized: "Copy", comment: "Context menu of a tool call: copies its line and detail"), systemImage: "doc.on.doc") {
                    UIPasteboard.general.string = [call.label, call.detail].compactMap { $0 }.joined(separator: "\n")
                }
            }
            .accessibilityLabel(accessibilityWords)
            .accessibilityValue(call.detail == nil ? "" : (isExpanded ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure")))
            if isExpanded, let detail = call.detail {
                ScrollView {
                    Text(detail)
                        .textRole(.code)
                        .foregroundStyle(Tone.secondary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(Space.s)
                }
                .frame(maxHeight: Metric.toolDetailMax)
                .fixedSize(horizontal: false, vertical: true)
                .background(Tone.nested, in: rounded(Radius.inner))
                .padding(.leading, Metric.iconSlot + Space.xs)
                .padding(.bottom, Space.xs)
                .transition(.opacity)
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: isExpanded)
    }

    @ViewBuilder private var line: some View {
        let content: some View = HStack(spacing: Space.xs) {
            Symbol(name: call.symbol, context: .tool).frame(width: Metric.iconSlot)
            Text(call.label).textRole(.footnote).lineLimit(dynamicTypeSize.isAccessibilitySize ? 4 : 1).truncationMode(.tail)
        }
        switch call.state {
        case .running: content.modifier(Shimmer())
        case .done: content.foregroundStyle(Tone.secondary)
        case .failed: content.foregroundStyle(Tone.secondary)
        }
    }

    private var accessibilityWords: String {
        switch call.state {
        case .running: String(localized: "\(call.label), running", comment: "VoiceOver: a tool call at work; the argument is its line")
        case .done: call.label
        case .failed: String(localized: "\(call.label), failed", comment: "VoiceOver: a tool call that failed; the argument is its line")
        }
    }
}

/// Consecutive tool calls as one group behind a 44 pt disclosure (review S4: the dense rows keep a safe target):
/// "Worked for 2m 14s · 5 steps"; a group with a failure is drawn open.
struct ToolGroup: View {
    let summary: String
    let calls: [ToolCall]
    @State private var isOpen: Bool
    @State private var expanded: Set<String> = []
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    init(summary: String, calls: [ToolCall], startsOpen: Bool) {
        self.summary = summary
        self.calls = calls
        _isOpen = State(initialValue: startsOpen)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Button {
                isOpen.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    Text(summary).textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers()
                    Symbol(name: "chevron.right", context: .status).foregroundStyle(Tone.glyph)
                        .rotationEffect(.degrees(isOpen ? 90 : 0))
                    Spacer(minLength: 0)
                }
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: isOpen)
            .accessibilityLabel(summary)
            .accessibilityValue(isOpen ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            .accessibilityHint(isOpen ? String(localized: "Hides the steps", comment: "VoiceOver hint of an open work-log group") : String(localized: "Shows the steps", comment: "VoiceOver hint of a closed work-log group"))
            if isOpen {
                ForEach(calls) { call in
                    ToolCallRow(
                        call: call,
                        isExpanded: Binding(
                            get: { expanded.contains(call.id) },
                            set: { open in
                                if open { expanded.insert(call.id) } else { expanded.remove(call.id) }
                            }))
                }
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: isOpen)
    }
}

/// Who speaks (plan §0: Dot orchestrates agents): the agent's face and name over the first line it says after someone
/// else spoke. Dot wears its own face; another agent a neutral disc.
struct SpeakerLine: View {
    let agent: String

    var body: some View {
        HStack(spacing: Space.xs) {
            if agent == "Dot" { DotFace(size: 20) } else { AgentAvatar(size: 20) }
            Text(agent).textRole(.label).fontWeight(.semibold).foregroundStyle(Tone.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(agent)
        .accessibilityAddTraits(.isHeader)
    }
}

/// The helpers a run started, as one group (R-e #2): each helper's face, name, current step and status word, behind a
/// 44 pt disclosure that says how many still work.
struct HelperGroup: View {
    let helpers: [HelperLine]
    let open: (HelperLine) -> Void
    @State private var isOpen: Bool = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    var body: some View {
        let working: Int = helpers.filter { $0.status == .working || $0.status == .waitingOnYou }.count
        VStack(alignment: .leading, spacing: 0) {
            Button {
                isOpen.toggle()
            } label: {
                HStack(spacing: Space.xs) {
                    Symbol(name: "person.2", context: .tool).foregroundStyle(Tone.secondary)
                        .frame(width: Metric.iconSlot)
                    Text(String(localized: "\(helpers.count) helpers", comment: "Helper group header, first part; the argument is how many helpers the run started") + " · " + String(localized: "\(working) working", comment: "Helper group header, second part; the argument is how many still work")).textRole(.footnote)
                        .foregroundStyle(Tone.secondary).tabularNumbers()
                    Symbol(name: "chevron.right", context: .status).foregroundStyle(Tone.glyph)
                        .rotationEffect(.degrees(isOpen ? 90 : 0))
                    Spacer(minLength: 0)
                }
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .haptic(.selection, trigger: isOpen)
            .accessibilityValue(isOpen ? String(localized: "Expanded", comment: "VoiceOver value of an open disclosure") : String(localized: "Collapsed", comment: "VoiceOver value of a closed disclosure"))
            if isOpen {
                ForEach(helpers) { helper in
                    Button {
                        open(helper)
                    } label: {
                        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                            AgentAvatar(size: 18).frame(width: Metric.iconSlot)
                                .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                            VStack(alignment: .leading, spacing: 1) {
                                Text(helper.agent).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
                                Text(helper.step).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2)
                            }
                            Spacer(minLength: Space.xs)
                            StatusWord(status: helper.status)
                        }
                        .padding(.vertical, Space.xxs)
                        .frame(minHeight: Metric.hit)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel([helper.agent, helper.status.word, helper.step].joined(separator: ", "))
                    .accessibilityAddTraits(.isButton)
                }
            }
        }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: isOpen)
    }
}

/// A helper as the helper group of a transcript shows it: who, its current step, its state.
struct HelperLine: Identifiable, Hashable, Sendable {
    let id: String
    let agent: String
    let step: String
    let status: ChatStatus
}
