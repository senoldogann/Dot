import DotEngine
import SwiftUI

/// How a run ended, when not done, with what the owner can do about it on the web's own routes (`views/run.js`):
/// - cut off by a restart of Dot (or the end of its Terminal command): what cut it, and Continue (`/runs/{id}/resume`);
/// - failed: the cause, Continue where the daemon says it can go on, and Retry (rewind to before the message and send
///   it again, item 1185), which asks first whether the files the run changed go back too;
/// - stopped by the Claude plan's usage limit: when the plan resets, and Resume now (the same `/resume`);
/// - stopped by the owner: the line alone.
/// Nothing is offered while a run of the chat works, and a card of an older run offers no Continue.
struct RunEndCard: View {
    enum Ending: Equatable {
        case ended(status: String, detail: String)
        case limit(resetsAt: Double?)
    }

    let item: String
    let ending: Ending
    let thread: String
    let toComposer: (String) -> Void
    let tell: (ToastMessage) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var busy: Bool = false
    @State private var confirmingRetry: Bool = false
    @State private var failure: String?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    private var view: ThreadView? { model.chats[thread]?.view }
    private var run: RunRecord? { runOfEndCard(item).flatMap { id in view?.runs.first { $0.id == id } } }
    private var offer: EndOffer {
        guard let run else { return EndOffer(resume: false, retry: false, cutOff: nil) }
        return endOffer(run: run, newest: view?.newest, chatWorking: view?.runs.contains { $0.isActive } ?? false)
    }

    var body: some View {
        let offer: EndOffer = offer
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            Symbol(name: glyph(offer), context: .feedback).foregroundStyle(failed && offer.cutOff == nil ? Tone.failure : Tone.secondary)
            VStack(alignment: .leading, spacing: Space.xs) {
                Text(title(offer)).textRole(.footnote).fontWeight(.medium).foregroundStyle(failed && offer.cutOff == nil ? Tone.failure : Tone.text)
                if let line = line(offer) { Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(4) }
                if let failure { Text(failure).textRole(.footnote).foregroundStyle(Tone.failure) }
                if offer.resume || offer.retry {
                    // At the accessibility sizes the buttons stand one under the other, none cut.
                    buttonsLayout {
                        if offer.resume {
                            Button(resumeTitle, action: resume).buttonStyle(SecondaryButtonStyle()).accessibilityIdentifier("end.resume")
                        }
                        if offer.retry {
                            Button(String(localized: "Retry", comment: "Run end card: takes the chat back to before the message and sends it again")) { confirmingRetry = true }
                                .buttonStyle(SecondaryButtonStyle()).accessibilityIdentifier("end.retry")
                        }
                        if busy { ProgressView().controlSize(.small) }
                    }
                    .disabled(busy)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Tone.nested, in: rounded(Radius.bubble))
        .accessibilityElement(children: .contain)
        .confirmationDialog(
            String(localized: "Retry this message?", comment: "Title of the confirmation of Retry"), isPresented: $confirmingRetry, titleVisibility: .visible
        ) {
            Button(String(localized: "Retry", comment: "Run end card: takes the chat back to before the message and sends it again")) { retry(.keep) }
            Button(String(localized: "Undo the file changes and retry", comment: "Retry: also puts back the files the run changed"), role: .destructive) { retry(.revert) }
        } message: {
            Text(
                String(
                    localized: "Take this chat back to before your message and send it again, with its files. The answer and what came after it leave the chat.",
                    comment: "Retry: what it does"))
        }
    }

    private var buttonsLayout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.xs)) : AnyLayout(HStackLayout(spacing: Space.xs))
    }

    private var failed: Bool {
        if case .ended(let status, _) = ending { return status == "failed" }
        return false
    }

    private var resumeTitle: String {
        if case .limit = ending { return String(localized: "Resume now", comment: "Usage limit card: starts the work again now") }
        return String(localized: "Continue", comment: "Run end card: goes on from where the run stopped")
    }

    private func glyph(_ offer: EndOffer) -> String {
        if offer.cutOff != nil { return "arrow.uturn.backward" }
        switch ending {
        case .limit: return "info.circle"
        case .ended(let status, _): return status == "failed" ? "exclamationmark.triangle" : "info.circle"
        }
    }

    private func title(_ offer: EndOffer) -> String {
        if let cut = offer.cutOff { return cutOffWords(cut) }
        switch ending {
        case .limit:
            return String(localized: "The Claude plan's usage limit was reached", comment: "Usage limit card title")
        case .ended(let status, _):
            switch status {
            case "failed": return String(localized: "The run failed", comment: "Notice at the end of a failed run")
            case "cancelled": return String(localized: "The run was stopped", comment: "Notice at the end of a stopped run")
            default: return String(localized: "The run ended", comment: "Notice at the end of a run that ended another way")
            }
        }
    }

    private func line(_ offer: EndOffer) -> String? {
        switch ending {
        case .limit(let resetsAt):
            guard let resetsAt else { return String(localized: "It resets within a few hours.", comment: "Usage limit card when the plan did not say when") }
            let clock: String = clockWords(Date(timeIntervalSince1970: resetsAt), format: TimestampFormat.current(.standard), locale: .current)
            return String(localized: "It resets at \(clock).", comment: "Usage limit card; the argument is the clock time")
        case .ended(_, let detail):
            if offer.cutOff != nil { return String(localized: "Continue picks it up where it was cut off.", comment: "Run end card of a run a restart cut off") }
            return detail.isEmpty ? nil : detail
        }
    }

    private func cutOffWords(_ cut: String) -> String {
        switch cut {
        case "daemon_restarted": String(localized: "Dot restarted while this task worked.", comment: "Run end card: a restart of Dot cut the run off")
        case "daemon_shutdown": String(localized: "Dot was shut down while this task worked.", comment: "Run end card: Dot was shut down under the run")
        case "process_ended": String(localized: "Its Terminal command was told to end while this task worked.", comment: "Run end card: the dotx command ended")
        default: String(localized: "Its Terminal command stopped suddenly while this task worked.", comment: "Run end card: the dotx command died")
        }
    }

    private var macPlace: String { String(localized: "the chat on the web", comment: "Where on the Mac a chat's action is done") }

    private func resume() {
        guard let run else { return }
        busy = true
        failure = nil
        Task {
            let outcome: Result<RunRecord, SettingsFailureBox> = await model.resume(run: run.id)
            busy = false
            switch outcome {
            case .success:
                tell(ToastMessage(id: UUID(), text: String(localized: "Continuing the task…", comment: "Toast: a resumed run started"), systemImage: "checkmark", actionTitle: nil))
            case .failure(let box):
                failure = failureWords(box.failure, macPlace: macPlace)
            }
        }
    }

    private func retry(_ files: RewindFiles) {
        guard let run else { return }
        busy = true
        failure = nil
        Task {
            let outcome: Result<RetryOutcome, SettingsFailureBox> = await model.retry(run: run, files: files, choices: view?.newest?.choices)
            busy = false
            switch outcome {
            case .success(.sent):
                tell(ToastMessage(id: UUID(), text: String(localized: "Sent again", comment: "Toast: Retry sent the message again"), systemImage: "checkmark", actionTitle: nil))
            case .success(.leftInComposer(let cut, let reason)):
                toComposer(cut.text)
                tell(
                    ToastMessage(
                        id: UUID(), text: String(localized: "Your message waits in the box: \(failureWords(reason, macPlace: macPlace))", comment: "Toast: Retry rewound but could not send; the argument is why"),
                        systemImage: "exclamationmark.triangle", actionTitle: nil))
            case .failure(let box):
                failure = failureWords(box.failure, macPlace: macPlace)
            }
        }
    }
}
