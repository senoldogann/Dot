import SwiftUI
import UIKit

/// The microphone of a composer: a press starts listening, another stops it; while it listens the button is the accent.
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
                    Image(systemName: "mic.fill").font(.system(size: 16)).foregroundStyle(Theme.accent)
                default:
                    Image(systemName: "mic").font(.system(size: 16)).foregroundStyle(Theme.muted)
                }
            }
            .frame(width: 36, height: 36)
            .background(dictation.state == .listening ? Theme.accent.opacity(0.14) : Color.clear, in: RoundedRectangle(cornerRadius: Theme.radius))
        }
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
            HStack(spacing: 6) {
                Circle().fill(Theme.accent).frame(width: 6, height: 6)
                Text("Listening… tap the microphone to stop").font(Theme.meta).foregroundStyle(Theme.muted)
                Spacer()
            }
            .padding(.horizontal, 16)
        case .failed(let words):
            HStack(spacing: 8) {
                Text(words).font(Theme.meta).foregroundStyle(Theme.warn).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                if dictation.blocked?.opensSettings == true {
                    Button("Settings") {
                        if let url: URL = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                    .font(Theme.meta)
                }
                Button { dictation.clearFailure() } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .semibold)) }
                    .foregroundStyle(Theme.faint)
                    .accessibilityLabel("Dismiss")
            }
            .padding(.horizontal, 16)
            .accessibilityIdentifier("dictation-failure")
        default:
            EmptyView()
        }
    }
}
