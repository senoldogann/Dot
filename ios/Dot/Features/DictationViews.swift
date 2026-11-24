import SwiftUI
import UIKit

/// The microphone of a composer: a press starts listening, another stops it; while it listens the button is the accent
/// and its waves move.
struct MicButton: View {
    let dictation: Dictation
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                switch dictation.state {
                case .starting:
                    ProgressView().controlSize(.small).tint(Theme.accent)
                case .listening:
                    Image(systemName: "waveform").font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.accent)
                        .symbolEffect(.variableColor.iterative, options: .repeating)
                default:
                    Image(systemName: "mic").font(.system(size: 16, weight: .medium)).foregroundStyle(Theme.muted)
                }
            }
            .frame(width: 36, height: 36)
            .background(dictation.state == .listening ? Theme.accent.opacity(0.16) : Color.clear, in: Circle())
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
                Image(systemName: "circle.fill").font(.system(size: 7)).foregroundStyle(Theme.accent)
                    .symbolEffect(.pulse, options: .repeating)
                Text("Listening… tap the microphone to stop").font(Theme.meta).foregroundStyle(Theme.muted)
                Spacer()
            }
            .padding(.horizontal, Space.l)
        case .failed(let words):
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 12)).foregroundStyle(Theme.warn)
                Text(words).font(Theme.meta).foregroundStyle(Theme.warn).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                if dictation.blocked?.opensSettings == true {
                    Button("Settings") {
                        if let url: URL = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                    .font(Theme.meta.weight(.semibold))
                }
                Button { dictation.clearFailure() } label: {
                    Image(systemName: "xmark").font(.system(size: 11, weight: .semibold)).frame(width: 32, height: 32)
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
