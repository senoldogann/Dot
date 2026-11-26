import AVFoundation
import DotEngine
import NaturalLanguage
import Observation
import SwiftUI
import UIKit

/// Reads a text aloud with the iPhone's own voice, in the language the text is written in (web `readaloud.js`, item
/// 574): one text at a time; nothing leaves the phone.
@MainActor
@Observable
final class ReadAloud: NSObject, AVSpeechSynthesizerDelegate {
    static let shared: ReadAloud = ReadAloud()
    /// The id of the message being read, `nil` when nothing is.
    private(set) var reading: String?
    @ObservationIgnored private let synthesizer: AVSpeechSynthesizer = AVSpeechSynthesizer()

    override private init() {
        super.init()
        synthesizer.delegate = self
    }

    func toggle(_ text: String, id: String) {
        if reading == id {
            stop()
            return
        }
        synthesizer.stopSpeaking(at: .immediate)
        let utterance: AVSpeechUtterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: Self.language(of: text))
        reading = id
        synthesizer.speak(utterance)
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
        reading = nil
    }

    /// The BCP 47 tag of the text's dominant language ("tr", "en"), the app's own when it cannot be told.
    static func language(of text: String) -> String {
        let recognizer: NLLanguageRecognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        return recognizer.dominantLanguage?.rawValue ?? Locale.current.language.languageCode?.identifier ?? "en"
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.reading = nil }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in if !self.synthesizer.isSpeaking { self.reading = nil } }
    }
}

/// The owner's message's actions (web `messagesheet.js`, item 1064): held, the message opens the system's menu with
/// when it was sent, Copy, Select text, Read aloud and Edit from here (item 282). Edit from here asks first whether the
/// files the later runs changed go back too, is offered only where the chat can go back to before the message, and is
/// greyed while a run works (the daemon refuses a rewind then). The message's words come back to the composer.
struct OwnerMessageActions: ViewModifier {
    let item: String
    let text: String
    let thread: String
    let toComposer: (String) -> Void
    let tell: (ToastMessage) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var selecting: Bool = false
    @State private var confirming: Bool = false
    @State private var rewinding: Bool = false
    @State private var failure: String?
    private let speaker: ReadAloud = ReadAloud.shared

    private var view: ThreadView? { model.chats[thread]?.view }
    private var run: RunRecord? { runOfOwnerMessage(item).flatMap { id in view?.runs.first { $0.id == id } } }
    private var working: Bool { view?.runs.contains { $0.isActive } ?? false }

    func body(content: Content) -> some View {
        content
            .contextMenu {
                if let run {
                    Section(sentWords(run.createdAt)) { menuItems(run: run) }
                } else {
                    menuItems(run: nil)
                }
            }
            .sheet(isPresented: $selecting) {
                ScrollView {
                    Text(text).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(Space.gutter)
                }
                .inspectorSheet(title: String(localized: "Select text", comment: "Owner's message menu: opens the message's words to select a part"), close: { selecting = false })
            }
            .confirmationDialog(
                String(localized: "Edit from here?", comment: "Title of the confirmation of Edit from here"), isPresented: $confirming, titleVisibility: .visible
            ) {
                Button(String(localized: "Keep the file changes", comment: "Edit from here: rewinds the chat and leaves the files as they are")) { rewind(.keep) }
                Button(String(localized: "Also undo the file changes", comment: "Edit from here: rewinds the chat and puts the files back"), role: .destructive) { rewind(.revert) }
            } message: {
                Text(String(localized: "Take this chat back to before this message. Your message and its files go back in the message box.", comment: "Edit from here: what it does"))
            }
            .alert(
                String(localized: "Edit from here did not work", comment: "Alert title: a rewind failed"),
                isPresented: Binding(get: { failure != nil }, set: { if !$0 { failure = nil } }), presenting: failure
            ) { _ in
                Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), role: .cancel) { failure = nil }
            } message: { words in
                Text(verbatim: words)
            }
    }

    @ViewBuilder private func menuItems(run: RunRecord?) -> some View {
        Button(String(localized: "Copy the text", comment: "Owner's message menu: copies its words"), systemImage: "doc.on.doc") {
            UIPasteboard.general.string = text
            tell(ToastMessage(id: UUID(), text: String(localized: "Copied", comment: "Code block header after a copy"), systemImage: "checkmark", actionTitle: nil))
        }
        Button(String(localized: "Select text", comment: "Owner's message menu: opens the message's words to select a part"), systemImage: "selection.pin.in.out") { selecting = true }
        Button(
            speaker.reading == item
                ? String(localized: "Stop reading", comment: "Message menu: stops reading the message aloud")
                : String(localized: "Read aloud", comment: "Message menu: reads the message aloud"),
            systemImage: speaker.reading == item ? "stop.circle" : "speaker.wave.2"
        ) { speaker.toggle(text, id: item) }
        if let run, ownerMessageOffer(run: run, chatWorking: false).canRewind {
            Button(String(localized: "Edit from here", comment: "Owner's message menu: takes the chat back to before this message"), systemImage: "arrow.uturn.backward") {
                confirming = true
            }
            .disabled(working || rewinding)
            if working {
                Text(String(localized: "Stop the run at work to edit from here.", comment: "Owner's message menu: why Edit from here is greyed"))
            }
        }
    }

    private func sentWords(_ at: Double) -> String {
        String(
            localized: "Sent \(Date(timeIntervalSince1970: at).formatted(date: .abbreviated, time: .shortened))",
            comment: "Owner's message menu header: when it was sent; the argument is the date and time")
    }

    private func rewind(_ files: RewindFiles) {
        guard let run else { return }
        rewinding = true
        Task {
            let outcome: Result<RewindView, SettingsFailureBox> = await model.editFromHere(run: run.id, files: files)
            rewinding = false
            switch outcome {
            case .success(let cut):
                toComposer(cut.text)
                tell(ToastMessage(id: UUID(), text: rewoundWords(cut), systemImage: "arrow.uturn.backward", actionTitle: nil))
            case .failure(let box):
                failure = failureWords(box.failure, macPlace: String(localized: "the chat on the web", comment: "Where on the Mac a chat's action is done"))
            }
        }
    }
}

/// What a rewind did, in one line: the files it took back and those it left (each says why on the web).
func rewoundWords(_ cut: RewindView) -> String {
    if !cut.left.isEmpty {
        return String(
            localized: "Back to before your message. Some files changed since and were left as they are.",
            comment: "Toast after Edit from here or Retry when some files could not be put back")
    }
    if !cut.reverted.isEmpty {
        return String(localized: "Back to before your message, the files put back.", comment: "Toast after Edit from here or Retry that put files back")
    }
    return String(localized: "Back to before your message.", comment: "Toast after Edit from here or Retry with no file to put back")
}
