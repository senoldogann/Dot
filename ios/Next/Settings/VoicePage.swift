import AVFoundation
import DotEngine
import SwiftUI

/// Settings › Voice (PLAN §25, owner-refs/ref-voice-settings.png): where a recording is written down (this phone, the
/// audio staying on it, or the paired Mac), the dictation language, and the preferred order of the microphones, which
/// the owner drags; a wired or Bluetooth microphone is named the first time it is dictated with, and a named one is
/// forgotten with a swipe. Voice input takes the first connected microphone of the list.
struct VoicePage: View {
    @Environment(AppModel.self) private var model: AppModel
    @AppStorage(TranscribeOn.key) private var onRaw: String = TranscribeOn.device.rawValue
    @AppStorage(PrefKey.dictationLanguage) private var languageRaw: String = DictationLanguage.app.rawValue
    @State private var order: [MicSlot] = MicSlot.read(.standard)
    @State private var macAbility: Loadable<VoiceAbility>?

    private var on: TranscribeOn { TranscribeOn(rawValue: onRaw) ?? .device }

    var body: some View {
        List {
            Section {
                RadioRow(
                    title: String(localized: "This device", comment: "Settings › Voice: transcribe on this phone"),
                    description: String(localized: "Audio stays on this device and is transcribed here.", comment: "Settings › Voice: what transcribing on the phone means"),
                    isSelected: on == .device
                ) { onRaw = TranscribeOn.device.rawValue }
                .accessibilityIdentifier("voice.device")
                RadioRow(
                    title: model.macName ?? String(localized: "Your Mac", comment: "Settings › Voice: the paired Mac when it has no name"),
                    description: model.host?.origin.absoluteString ?? String(localized: "Not paired", comment: "Settings › Voice: no Mac is paired"),
                    isSelected: on == .mac
                ) { onRaw = TranscribeOn.mac.rawValue }
                .accessibilityIdentifier("voice.mac")
            } header: {
                header(String(localized: "Transcribe on", comment: "Settings › Voice section: where a recording is written down"))
            } footer: {
                if on == .mac, let why = macWhy { footer(why) }
            }
            .listRowInsets(EdgeInsets())
            .listRowBackground(Tone.card)

            Section {
                ForEach(DictationLanguage.allCases, id: \.self) { language in
                    RadioRow(title: language.title, description: nil, isSelected: languageRaw == language.rawValue) { languageRaw = language.rawValue }
                }
            } header: {
                header(String(localized: "Language", comment: "Settings › Voice section: the dictation language"))
            }
            .listRowInsets(EdgeInsets())
            .listRowBackground(Tone.card)

            Section {
                ForEach(order, id: \.self) { slot in
                    micRow(slot)
                        .swipeActions(edge: .trailing) {
                            if let uid = slot.namedUID {
                                Button(String(localized: "Forget", comment: "Settings › Voice: removes a named microphone from the list"), systemImage: "trash", role: .destructive) {
                                    order = forgetting(uid, in: order)
                                    MicSlot.write(order, .standard)
                                }
                            }
                        }
                }
                .onMove { from, to in
                    order.move(fromOffsets: from, toOffset: to)
                    MicSlot.write(order, .standard)
                }
            } header: {
                header(String(localized: "Preferred order", comment: "Settings › Voice section: the order the microphones are taken in"))
            } footer: {
                footer(String(localized: "Voice input uses the first connected microphone in this list; hold and drag to reorder. A wired or Bluetooth microphone is added by name the first time you dictate with it connected; until then it falls under Other. Swipe left on a named microphone to forget it.", comment: "Settings › Voice: how the preferred order works"))
            }
            .listRowInsets(EdgeInsets())
            .listRowBackground(Tone.card)
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Tone.ground)
        .navigationTitle(String(localized: "Voice", comment: "Settings row: voice typing"))
        .navigationBarTitleDisplayMode(.inline)
        .task(id: onRaw) {
            guard on == .mac, macAbility?.value == nil else { return }
            macAbility = await model.read { api throws(DaemonRequestError) in try await api.voiceAbility() }
        }
        .onAppear { order = MicSlot.read(.standard) }
    }

    /// Why the Mac cannot write a recording down now, in its words, or how to read it.
    private var macWhy: String? {
        switch macAbility {
        case .loaded(let ability): ability.transcribe ? nil : ability.why
        case .failed(let failure): failureWords(failure, macPlace: model.macName ?? "")
        case .loading, nil: nil
        }
    }

    private func header(_ text: String) -> some View {
        Text(text).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary).textCase(nil)
    }

    private func footer(_ text: String) -> some View {
        Text(text).textRole(.footnote).foregroundStyle(Tone.tertiary)
    }

    private func micRow(_ slot: MicSlot) -> some View {
        let (symbol, title, detail): (String, String, String) =
            switch slot {
            case .named(_, let name, let kind):
                (
                    kind == .wired ? "cable.connector" : "headphones", name,
                    kind == .wired
                        ? String(localized: "Wired", comment: "Settings › Voice: a named wired microphone")
                        : String(localized: "Bluetooth", comment: "Settings › Voice: a named Bluetooth microphone")
                )
            case .otherWired:
                (
                    "cable.connector", String(localized: "Other wired", comment: "Settings › Voice: wired microphones not named in the list"),
                    String(localized: "Headsets and USB microphones not listed here", comment: "Settings › Voice: what Other wired covers")
                )
            case .otherBluetooth:
                (
                    "headphones", String(localized: "Other Bluetooth", comment: "Settings › Voice: Bluetooth microphones not named in the list"),
                    String(localized: "AirPods, headsets and cars not listed here", comment: "Settings › Voice: what Other Bluetooth covers")
                )
            case .builtIn:
                (
                    "iphone", String(localized: "Built-in microphone", comment: "Settings › Voice: the phone's own microphone"),
                    String(localized: "The microphone on this device", comment: "Settings › Voice: what the built-in microphone is")
                )
            case .carPlay:
                ("car", String(localized: "CarPlay", comment: "Settings › Voice: the car's microphone through CarPlay"), String(localized: "The car's microphone", comment: "Settings › Voice: what CarPlay's microphone is"))
            }
        return HStack(spacing: 0) {
            ListRow(systemImage: symbol, title: title, description: detail, accessory: .none)
            // The drag handle the reference draws; the whole row drags (hold, then move).
            Image(systemName: "line.3.horizontal").font(.system(.body, weight: .regular)).foregroundStyle(Tone.tertiary)
                .padding(.trailing, Space.groupedGutter)
                .accessibilityHidden(true)
        }
        .accessibilityIdentifier("voice.mic")
    }
}
