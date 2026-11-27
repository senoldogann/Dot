import SwiftUI

/// The message a routine sent into a chat (a schedule's, a watcher's or a hook's run; owner 2026-11-27, after T3's
/// "Automation" row): one compact row on the owner's side, the calendar-clock glyph, "Routine" in grey (the phone's word
/// for schedules, the bot profile's Routines), the routine's title cut at its end and a chevron; a tap unfolds the whole
/// message under them. Never an owner's bubble: the owner did not write it.
struct RoutineRow: View {
    let title: String
    let prompt: String
    @State private var unfolded: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        let large: Bool = dynamicTypeSize.isAccessibilitySize
        TrailingFractionLayout(fraction: Metric.bubbleFraction) {
            Button {
                withAnimation(motionAnimation(.disclosure, reduceMotion: reduceMotion)) { unfolded.toggle() }
            } label: {
                VStack(alignment: .leading, spacing: Space.xs) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        Symbol(name: "calendar.badge.clock", context: .row).foregroundStyle(Tone.secondary)
                        Text(String(localized: "Routine", comment: "Chat: the label of a message a schedule, watcher or hook sent"))
                            .textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1).fixedSize()
                        Text(title).textRole(.footnote).foregroundStyle(Tone.text)
                            .lineLimit(large ? 3 : 1).truncationMode(.tail)
                        Spacer(minLength: 0)
                        Symbol(name: unfolded ? "chevron.up" : "chevron.down", context: .chevron).foregroundStyle(Tone.glyph)
                    }
                    if unfolded {
                        Text(prompt).textRole(.footnote).foregroundStyle(Tone.text).frame(maxWidth: .infinity, alignment: .leading)
                            .transition(.opacity)
                    }
                }
                .padding(.horizontal, Space.m)
                .padding(.vertical, Space.s)
                .frame(minHeight: Metric.hit)
                .background(Tone.nested, in: rounded(unfolded ? Radius.bubble : Metric.hit / 2))
                .contentShape(rounded(Radius.bubble))
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(String(localized: "Routine: \(title)", comment: "VoiceOver: a routine's message in a chat; the argument is its title"))
        .accessibilityValue(unfolded ? prompt : "")
        .accessibilityHint(
            unfolded
                ? String(localized: "Hides the message", comment: "VoiceOver hint of an unfolded routine row")
                : String(localized: "Shows the whole message", comment: "VoiceOver hint of a folded routine row"))
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("routine.row")
    }
}
