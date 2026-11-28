import DotEngine
import SwiftUI

/// How a run ended, when not done, with what the owner can do about it on the web's own routes (`views/run.js`):
/// - cut off by a restart of Dot (or the end of its Terminal command): what cut it, and Continue (`/runs/{id}/resume`);
/// - failed: the cause, Continue where the daemon says it can go on, and Retry (rewind to before the message and send
///   it again, item 1185), which asks first whether the files the run changed go back too;
/// - stopped by the Claude plan's usage limit: when the plan resets, and Resume now (the same `/resume`);
/// - waiting on the owner (item 2070): "Waiting for you" and what it needs, no red; its card above stays answerable;
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
        // Drawn as plainly as the work log's rows (item 1904, the owner): a small muted symbol, one line "The run failed ·
        // <reason>", the actions as plain text buttons on the row; no card, no red title. At the accessibility sizes
        // the actions stand under the line, one under the other.
        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
            Symbol(name: glyph(offer), context: .tool)
                .foregroundStyle(failed && offer.cutOff == nil ? Tone.failure.opacity(0.75) : Tone.glyph)
                .frame(width: Metric.iconSlot)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text([title(offer), line(offer)].compactMap { $0 }.joined(separator: " · "))
                    .textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(4)
                    .accessibilityIdentifier("end.line")
                if let failure { Text(failure).textRole(.footnote).foregroundStyle(Tone.failure) }
                if dynamicTypeSize.isAccessibilitySize { actions(offer) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !dynamicTypeSize.isAccessibilitySize { actions(offer) }
        }
        .frame(minHeight: Metric.toolRow)
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

    /// Continue and Retry as plain text buttons, 44 pt each way.
    @ViewBuilder private func actions(_ offer: EndOffer) -> some View {
        if offer.resume || offer.retry {
            buttonsLayout {
                if offer.resume {
                    Button(resumeTitle, action: resume).buttonStyle(PlainRowButtonStyle()).accessibilityIdentifier("end.resume")
                }
                if offer.retry {
                    Button(String(localized: "Retry", comment: "Run end card: takes the chat back to before the message and sends it again")) { confirmingRetry = true }
                        .buttonStyle(PlainRowButtonStyle()).accessibilityIdentifier("end.retry")
                }
                if busy { ProgressView().controlSize(.small) }
            }
            .disabled(busy)
        }
    }

    private var buttonsLayout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 0)) : AnyLayout(HStackLayout(spacing: Space.xs))
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
        case .ended(let status, _):
            if status == waitingEnd { return "hand.raised" }
            return status == "failed" ? "exclamationmark.triangle" : "info.circle"
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
            case waitingEnd: return String(localized: "Waiting for you", comment: "Notice at the end of a run that waits on the owner")
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

/// A text button on a row (item 1904): the footnote in the ink, medium weight, a 44 pt target, no fill.
struct PlainRowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
            .opacity(configuration.isPressed ? 0.5 : 1)
            .frame(minWidth: Metric.hit, minHeight: Metric.hit)
            .contentShape(Rectangle())
    }
}
