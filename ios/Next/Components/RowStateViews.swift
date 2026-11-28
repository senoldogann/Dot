import DotEngine
import SwiftUI

/// The words a row says for where it stands (item 1920), `nil` at rest (the row then shows its last line).
func rowStateWords(_ state: RowState) -> String? {
    switch state {
    case .needsYou: String(localized: "Needs you", comment: "Chat row: an approval, a question or a secret waits for the owner")
    case .working: String(localized: "Working", comment: "Chat row: a run of the chat is at work")
    case .waitingOnHelpers(let count):
        String(localized: "Waiting for \(count) helpers", comment: "Chat row: the run waits for its helpers; the argument is how many")
    case .waiting(let what): String(localized: "Waiting · \(what)", comment: "Chat row: something works for the chat in the background; the argument names it")
    case .rest: nil
    }
}

/// A row's second line (item 1920): where it stands when something is going on, else its last line. What waits on the
/// owner is the strongest, in the accent; work in progress shimmers as the work log's live line does; what waits in
/// the background is quiet.
struct RowStateLine: View {
    let state: RowState
    let preview: String
    let lineLimit: Int

    var body: some View {
        switch state {
        case .needsYou:
            Text(rowStateWords(state) ?? "").textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.action).lineLimit(lineLimit)
        case .working, .waitingOnHelpers:
            Text((rowStateWords(state) ?? "") + "…").textRole(.footnote).lineLimit(lineLimit).modifier(Shimmer())
        case .waiting:
            Text(rowStateWords(state) ?? "").textRole(.footnote).foregroundStyle(Tone.tertiary).lineLimit(lineLimit)
        case .rest:
            Text(preview).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(lineLimit)
        }
    }
}

/// A row's top right (item 1920): a small live mark while a run of it works, its time otherwise.
struct RowTrailing: View {
    let state: RowState
    let time: String

    var body: some View {
        if state.isLive {
            LiveMark().accessibilityHidden(true)
        } else {
            Text(time).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers()
        }
    }
}

/// Three small dots that brighten in turn, in the ink of the time they stand for; still under Reduce Motion.
struct LiveMark: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    static let dot: CGFloat = 4
    static let period: Double = 1.2

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 15, paused: reduceMotion)) { timeline in
            let phase: Double = reduceMotion ? 0 : timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: Self.period) / Self.period
            HStack(spacing: 3) {
                ForEach(0..<3, id: \.self) { index in
                    Circle()
                        .fill(Tone.secondary)
                        .frame(width: Self.dot, height: Self.dot)
                        .opacity(liveDotOpacity(index: index, phase: phase, still: reduceMotion))
                }
            }
        }
        .frame(minWidth: 20, minHeight: 12)
        .accessibilityIdentifier("row.live")
    }
}

/// How bright the `index`th of the live mark's three dots is at `phase` (0 to 1 of a period): the lit one full, the
/// others dim; all half under Reduce Motion (pure).
func liveDotOpacity(index: Int, phase: Double, still: Bool) -> Double {
    if still { return 0.6 }
    let lit: Int = min(2, Int(phase * 3))
    return index == lit ? 1 : 0.3
}

/// The ring around Dot's face on the home (item 1920): the accent while something waits on the owner, a slow quiet
/// arc while one of its chats works, nothing otherwise.
struct BotStateRing: View {
    let state: RowState
    let size: CGFloat
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool

    var body: some View {
        switch state {
        case .needsYou:
            Circle().stroke(Tone.action, lineWidth: 3).frame(width: size, height: size).accessibilityIdentifier("dot.ring.needsYou")
        case .working, .waitingOnHelpers:
            TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { timeline in
                let turn: Double = reduceMotion ? 0 : timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 2) / 2
                Circle()
                    .trim(from: 0, to: 0.28)
                    .stroke(Tone.secondary, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(turn * 360 - 90))
                    .frame(width: size, height: size)
            }
            .accessibilityIdentifier("dot.ring.working")
        case .waiting, .rest:
            EmptyView()
        }
    }
}
