import SwiftUI

/// The primary action: an ink capsule with the ground's colour as its label (D5: no accent hue), 44 pt tall.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .textRole(.body).fontWeight(.medium)
            .foregroundStyle(isEnabled ? Tone.onAction : Tone.tertiary)
            .padding(.horizontal, Space.xl)
            .frame(minHeight: Metric.hit)
            .background(isEnabled ? Tone.action : Tone.nested, in: Capsule())
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(Capsule())
    }
}

/// A secondary action: the nested fill with the ink as its label.
struct SecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .textRole(.body).fontWeight(.medium)
            .foregroundStyle(isEnabled ? Tone.text : Tone.tertiary)
            .padding(.horizontal, Space.xl)
            .frame(minHeight: Metric.hit)
            .background(Tone.nested, in: Capsule())
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(Capsule())
    }
}

/// A destructive action: red words on a neutral fill, never a red fill (review S3: the words and the confirmation
/// carry the danger).
struct DestructiveButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .textRole(.body).fontWeight(.medium)
            .foregroundStyle(isEnabled ? Tone.failure : Tone.tertiary)
            .padding(.horizontal, Space.xl)
            .frame(minHeight: Metric.hit)
            .background(Tone.nested, in: Capsule())
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(Capsule())
    }
}

/// A chip (a filter, a model): a capsule at least 28 pt tall drawn inside a 44 pt target; chosen = ink fill.
struct Chip: View {
    let title: String
    let systemImage: String?
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.xxs + 1) {
                if let systemImage { Symbol(name: systemImage, context: .tool) }
                Text(title).textRole(.footnote).fontWeight(.medium).lineLimit(1)
            }
            .foregroundStyle(isSelected ? Tone.onAction : Tone.secondary)
            .padding(.horizontal, Space.s)
            .padding(.vertical, Space.xxs + 1.75)
            .frame(minHeight: 28)
            .background(isSelected ? Tone.action : Tone.nested, in: Capsule())
            .frame(minHeight: Metric.hit)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }
}

/// The state of a chat as one word (plan §2): the order is the priority when several hold at once.
enum ChatStatus: Int, CaseIterable, Comparable, Sendable {
    case waitingOnYou = 0
    case question = 1
    case working = 2
    case starting = 3
    case failed = 4
    case done = 5

    static func < (lhs: ChatStatus, rhs: ChatStatus) -> Bool { lhs.rawValue < rhs.rawValue }

    /// The one word a row shows when several states hold: the first by priority, or none.
    static func shown(of states: Set<ChatStatus>) -> ChatStatus? { states.min() }

    var word: String {
        switch self {
        case .waitingOnYou: String(localized: "Waiting on you", comment: "Status word: a chat waits for the owner's approval or answer")
        case .question: String(localized: "Question", comment: "Status word: Dot asked the owner a question")
        case .working: String(localized: "Working", comment: "Status word: a run is at work")
        case .starting: String(localized: "Starting", comment: "Status word: a run is starting")
        case .failed: String(localized: "Failed", comment: "Status word: the run failed")
        case .done: String(localized: "Done", comment: "Status word: the run finished")
        }
    }

    /// Amber only for what waits on the owner, red only for a failure, neutral otherwise.
    var tone: StatusTone {
        switch self {
        case .waitingOnYou, .question: .warn
        case .failed: .failure
        case .working, .starting: .neutral
        case .done: .quiet
        }
    }
}

enum StatusTone: Sendable {
    case warn
    case failure
    case neutral
    case quiet

    var color: Color {
        switch self {
        case .warn: Tone.warn
        case .failure: Tone.failure
        case .neutral: Tone.secondary
        case .quiet: Tone.tertiary
        }
    }
}

/// A status word: a small mark and the word, no capsule. Working shows the system's small spinner (still under Reduce
/// Motion, as the system draws it).
struct StatusWord: View {
    let status: ChatStatus

    var body: some View {
        HStack(spacing: Space.xxs + 1) {
            switch status {
            case .working, .starting:
                ProgressView().controlSize(.mini).tint(Tone.secondary)
            case .waitingOnYou, .question:
                Symbol(name: "circle.fill", context: .status).imageScale(.small)
            case .failed:
                Symbol(name: "xmark.circle.fill", context: .status)
            case .done:
                Symbol(name: "checkmark.circle.fill", context: .status)
            }
            Text(status.word).textRole(.label).fontWeight(.medium).lineLimit(1).fixedSize(horizontal: true, vertical: false)
        }
        .foregroundStyle(status.tone.color)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.word)
    }
}
