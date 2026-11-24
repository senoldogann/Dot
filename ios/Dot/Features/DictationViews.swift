import SwiftUI
import UIKit

/// The microphone of a composer: a press starts listening, another stops it; while it listens the button is a raised disc
/// and its waves move.
struct MicButton: View {
    let dictation: Dictation
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                switch dictation.state {
                case .starting:
                    ProgressView().controlSize(.small).tint(Theme.text)
                case .listening:
                    DotIcon(.audioWaveform, size: 19, pulse: true).foregroundStyle(Theme.text)
                default:
                    DotIcon(.mic, size: 19).foregroundStyle(Theme.muted)
                }
            }
            .frame(width: 36, height: 36)
            .background(dictation.state == .listening ? Theme.raised : Color.clear, in: Circle())
            .frame(width: 44, height: 44)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.impact(weight: .light), trigger: dictation.state.isActive)
        .accessibilityIdentifier("mic")
        .accessibilityLabel(dictation.state.isActive ? "Stop dictation" : "Dictate")
    }
}

/// The line over the box while dictation listens or when it failed (with a way to the app's page in Settings when a
/// permission is the reason).
struct DictationLine: View {
    let dictation: Dictation

    var body: some View {
        switch dictation.state {
        case .listening:
            HStack(spacing: Space.s) {
                Circle().fill(Theme.text).frame(width: 7, height: 7).modifier(DictationPulse())
                Text("Listening… tap the microphone to stop").font(Theme.meta).foregroundStyle(Theme.muted)
                Spacer()
            }
            .padding(.horizontal, Space.l)
        case .failed(let words):
            HStack(spacing: 8) {
                DotIcon(.alert, size: 14, relativeTo: .caption).foregroundStyle(Theme.bad)
                Text(words).font(Theme.meta).foregroundStyle(Theme.text).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                if dictation.blocked?.opensSettings == true {
                    Button("Settings") {
                        if let url: URL = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                    .font(Theme.meta.weight(.semibold))
                }
                Button { dictation.clearFailure() } label: {
                    DotIcon(.x, size: 13, relativeTo: .caption).frame(width: 32, height: 32)
                }
                .foregroundStyle(Theme.faint)
                    .accessibilityLabel("Dismiss")
            }
            .padding(.horizontal, Space.l)
            .accessibilityIdentifier("dictation-failure")
        default:
            EmptyView()
        }
    }
}

/// The dot of a recording breathes while it listens, and stands still when the reader asked for less motion.
private struct DictationPulse: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @State private var dim: Bool = false

    func body(content: Content) -> some View {
        content
            .opacity(dim && !reduceMotion ? 0.3 : 1)
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: dim)
            .onAppear { dim = true }
    }
}
