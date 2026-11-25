import SwiftUI

/// The composer while it records (PLAN §24, owner-refs/ref-voice-recording.png): ✕ at the leading edge, the live
/// waveform, the running clock and the ink ✓ at the trailing edge; above them, quietly, the words this phone heard so
/// far. While the words are written down the ✓ turns into a spinner.
struct VoiceRecorderBar: View {
    let levels: [Float]
    let startedAt: Date?
    let heard: String
    let transcribing: Bool
    let cancel: () -> Void
    let confirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(heard.isEmpty ? String(localized: "Listening…", comment: "Composer recorder: nothing heard yet") : heard)
                .textRole(.body).foregroundStyle(heard.isEmpty ? Tone.tertiary : Tone.secondary)
                .lineLimit(3).truncationMode(.head)
                .frame(maxWidth: .infinity, minHeight: 46, alignment: .topLeading)
                .padding(.horizontal, Space.m - Space.xxs)
                .padding(.top, Space.m)
                .accessibilityIdentifier("recorder.heard")
            HStack(spacing: Space.xs) {
                Button(action: cancel) {
                    Image(systemName: "xmark").font(.system(.body, weight: .medium)).foregroundStyle(Tone.text)
                        .frame(width: Metric.hit, height: Metric.hit).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(transcribing)
                .accessibilityLabel(String(localized: "Cancel the recording", comment: "VoiceOver: the recorder's cross, nothing is written down"))
                .accessibilityIdentifier("recorder.cancel")
                Waveform(levels: levels)
                    .frame(height: 22)
                    .accessibilityHidden(true)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let seconds: Int = startedAt.map { max(0, Int(context.date.timeIntervalSince($0))) } ?? 0
                    Text(verbatim: recordingClock(seconds)).textRole(.label).foregroundStyle(Tone.secondary).tabularNumbers().fixedSize()
                        .accessibilityLabel(String(localized: "Recording, \(seconds) seconds", comment: "VoiceOver: the recorder's clock; the argument is the seconds"))
                }
                .accessibilityIdentifier("recorder.clock")
                Button(action: confirm) {
                    Group {
                        if transcribing {
                            ProgressView().controlSize(.small).tint(Tone.onAction)
                        } else {
                            Image(systemName: "checkmark").font(.system(.callout, weight: .semibold)).foregroundStyle(Tone.onAction)
                        }
                    }
                    .frame(width: Metric.sendDisc, height: Metric.sendDisc)
                    .background(Tone.action, in: Circle())
                    .frame(width: Metric.hit, height: Metric.hit)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(transcribing)
                .accessibilityLabel(
                    transcribing
                        ? String(localized: "Writing it down…", comment: "VoiceOver: the recorder while the words are written down")
                        : String(localized: "Done, write it down", comment: "VoiceOver: the recorder's tick, the words go into the message"))
                .accessibilityIdentifier("recorder.confirm")
            }
        }
    }
}

/// The live waveform: the newest levels at the trailing edge as thin bars; the rest of the width a dotted line of
/// silence, as the reference draws it.
struct Waveform: View {
    let levels: [Float]

    var body: some View {
        Canvas { context, size in
            let step: CGFloat = 4
            let count: Int = Int(size.width / step)
            let shown: [Float] = Array(levels.suffix(count))
            let start: Int = count - shown.count
            let ink: GraphicsContext.Shading = .color(Tone.secondary)
            for index in 0..<count {
                let x: CGFloat = CGFloat(index) * step + 1
                if index < start {
                    context.fill(Path(ellipseIn: CGRect(x: x, y: size.height / 2 - 1, width: 2, height: 2)), with: .color(Tone.tertiary))
                } else {
                    let level: CGFloat = CGFloat(shown[index - start])
                    let height: CGFloat = max(2, level * size.height)
                    context.fill(Path(roundedRect: CGRect(x: x, y: (size.height - height) / 2, width: 2, height: height), cornerRadius: 1), with: ink)
                }
            }
        }
    }
}
