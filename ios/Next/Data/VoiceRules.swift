import Foundation

// Settings › Voice (PLAN §25, owner-refs/ref-voice-settings.png), its pure rules: where the words are written down, and
// which microphone voice input takes. The owner orders the kinds of microphone; a wired or Bluetooth one is named in the
// list the first time it is dictated with, until then it is one of "Other wired" / "Other Bluetooth". Voice input takes
// the first connected microphone of the list (`VoiceInput` sets it as the audio session's preferred input).

/// Where the words of a recording are written down.
enum TranscribeOn: String, CaseIterable, Sendable {
    /// This phone's own recognizer (Speech, on the device): the audio stays on the phone.
    case device
    /// The paired Mac's dictation (`/api/voice/streams`): the audio goes to the owner's Mac and nowhere else.
    case mac

    static let key: String = "voice.transcribeOn"

    static func current(_ defaults: UserDefaults) -> TranscribeOn {
        TranscribeOn(rawValue: defaults.string(forKey: key) ?? "") ?? .device
    }
}

/// What kind of microphone a port is.
enum MicKind: String, Codable, Sendable, Equatable {
    case wired
    case bluetooth
    case builtIn
    case carPlay
}

/// A microphone the phone can take now (`AVAudioSession.availableInputs`), as the rules read it.
struct MicPort: Equatable, Sendable {
    let uid: String
    let name: String
    let kind: MicKind
}

/// One place of the preferred order.
enum MicSlot: Codable, Hashable, Sendable {
    /// A microphone learned by name: wired or Bluetooth.
    case named(uid: String, name: String, kind: MicKind)
    case otherWired
    case otherBluetooth
    case builtIn
    case carPlay

    /// The order before the owner changed it (the reference's): a plugged-in microphone first, the car's last.
    static let initial: [MicSlot] = [.otherWired, .otherBluetooth, .builtIn, .carPlay]

    static let key: String = "voice.micOrder"

    static func read(_ defaults: UserDefaults) -> [MicSlot] {
        guard let data: Data = defaults.data(forKey: key), let order = try? JSONDecoder().decode([MicSlot].self, from: data), !order.isEmpty else {
            return initial
        }
        return order
    }

    static func write(_ order: [MicSlot], _ defaults: UserDefaults) {
        if let data: Data = try? JSONEncoder().encode(order) { defaults.set(data, forKey: key) }
    }

    var namedUID: String? {
        if case .named(let uid, _, _) = self { return uid }
        return nil
    }
}

/// The microphone voice input takes: the first slot of `order` that a connected port fills. A named slot takes its own
/// port; an "Other" slot any port of its kind not named in the list. `nil` when no port fits (the system's choice then).
func preferredMic(_ ports: [MicPort], order: [MicSlot]) -> MicPort? {
    let named: Set<String> = Set(order.compactMap(\.namedUID))
    for slot in order {
        let found: MicPort? =
            switch slot {
            case .named(let uid, _, _): ports.first { $0.uid == uid }
            case .otherWired: ports.first { $0.kind == .wired && !named.contains($0.uid) }
            case .otherBluetooth: ports.first { $0.kind == .bluetooth && !named.contains($0.uid) }
            case .builtIn: ports.first { $0.kind == .builtIn }
            case .carPlay: ports.first { $0.kind == .carPlay }
            }
        if let found { return found }
    }
    return nil
}

/// The order after a dictation with `port`: a wired or Bluetooth microphone not yet named goes in by name just above its
/// "Other" slot, so it keeps the place its kind had. Anything else leaves the order as it is.
func learning(_ port: MicPort, into order: [MicSlot]) -> [MicSlot] {
    guard port.kind == .wired || port.kind == .bluetooth, !order.contains(where: { $0.namedUID == port.uid }) else { return order }
    let other: MicSlot = port.kind == .wired ? .otherWired : .otherBluetooth
    var next: [MicSlot] = order
    let at: Int = next.firstIndex(of: other) ?? next.endIndex
    next.insert(.named(uid: port.uid, name: port.name, kind: port.kind), at: at)
    return next
}

/// The order without a named microphone (swiped away); the four kinds stay.
func forgetting(_ uid: String, in order: [MicSlot]) -> [MicSlot] {
    order.filter { $0.namedUID != uid }
}

/// The language tag the Mac's dictation is asked for (`tr-TR`, `en-US`).
func languageTag(_ locale: Locale) -> String {
    let language: String = locale.language.languageCode?.identifier ?? "en"
    let region: String? = locale.region?.identifier
    if let region { return language + "-" + region }
    return language == "tr" ? "tr-TR" : language == "en" ? "en-US" : language
}

/// A recognizer's error as the rules read it: its domain and code, and its words for the owner.
struct RecognizerError: Equatable, Sendable {
    let domain: String
    let code: Int
    let detail: String

    init(domain: String, code: Int, detail: String) {
        self.domain = domain
        self.code = code
        self.detail = detail
    }

    init(_ error: Error) {
        let ns: NSError = error as NSError
        self.init(domain: ns.domain, code: ns.code, detail: ns.localizedDescription)
    }

    /// "No speech detected" (Speech's assistant domain, 1110): a silence, not a failure.
    var isSilence: Bool { domain == "kAFAssistantErrorDomain" && code == 1110 }
}

/// What a segment's end leads to while the owner records.
enum VoiceSegmentNext: Equatable, Sendable {
    /// A new segment on the same microphone.
    case goOn
    /// Silences past the limit with no word: the go ends with what was heard.
    case stopQuiet
    /// A real failure of the recognizer: the go ends, its words said.
    case stopFailed
}

/// A pause the recognizer closed a segment on goes on; a silence goes on up to `limit` times in a row with no word; any
/// other error ends the go (never a silent loop of new segments that hear nothing).
func voiceSegmentNext(final: Bool, error: RecognizerError?, quietRuns: Int, limit: Int) -> VoiceSegmentNext {
    guard let error else { return .goOn }
    guard error.isSilence else { return .stopFailed }
    return quietRuns < limit ? .goOn : .stopQuiet
}

/// A recording's clock: "0:07", "1:42", "12:03".
func recordingClock(_ seconds: Int) -> String {
    String(format: "%d:%02d", seconds / 60, seconds % 60)
}

/// The Mac takes at most five minutes in a stream: the recorder ends a little before.
let macRecordingLimit: TimeInterval = 290

/// A level of the microphone, 0 to 1, from the root mean square of a buffer's samples: -50 dB and below is silence.
func micLevel(rms: Float) -> Float {
    guard rms > 0 else { return 0 }
    let decibels: Float = 20 * log10(rms)
    return min(1, max(0, (decibels + 50) / 50))
}
