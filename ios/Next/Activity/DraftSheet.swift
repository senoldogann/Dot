import DotEngine
import SwiftUI

/// A draft a run wrote (item 314), as its card in a chat: to whom, the subject and the words, which the owner may
/// change before Send. Nothing goes out before Send, and then Mail or Messages gets what the sheet holds; a send the
/// application refused says why and can be sent again; Discard drops it. Both ask first. `settled` names a draft that
/// no longer waits (sent, discarded, answered elsewhere), for the list to drop it.
struct DraftSheet: View {
    let draft: DraftView
    let actions: ActivityActions
    let settled: (String) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var subject: String = ""
    @State private var words: String = ""
    @State private var sending: Bool = false
    @State private var askingSend: Bool = false
    @State private var askingDiscard: Bool = false
    @State private var askingClose: Bool = false
    @State private var problem: SettingsFailure?
    /// What the application said of the last send, when it refused it.
    @State private var refusal: String?

    private var isMail: Bool { draft.channel != "message" }
    private var dirty: Bool { subject != draft.subject || words != draft.body }
    private var recipients: String { draft.to.joined(separator: ", ") }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                if let problem { FailureBanner(failure: problem, macPlace: activityMacPlace, retry: nil) }
                if let refusal {
                    Notice(tone: .failure, message: String(localized: "Not sent: \(refusal)", comment: "Draft: the application refused the send; its words"), dismiss: nil)
                }
                GroupedSection(header: nil, footer: nil) {
                    ListRow(systemImage: isMail ? "envelope" : "message", title: String(localized: "To \(recipients)", comment: "Draft: its recipients"), description: nil, accessory: .none)
                    if isMail && !draft.cc.isEmpty {
                        ListRow(systemImage: nil, title: String(localized: "Cc \(draft.cc.joined(separator: ", "))", comment: "Draft: its copies"), description: nil, accessory: .none)
                    }
                }
                if isMail {
                    GroupedSection(header: String(localized: "Subject", comment: "Draft: the subject of a mail"), footer: nil) {
                        TextField(String(localized: "Subject", comment: "Draft: the subject of a mail"), text: $subject).textRole(.body).padding(Space.groupedGutter)
                    }
                }
                GroupedSection(header: String(localized: "Words", comment: "Draft: the body of a mail or a message"), footer: nil) {
                    TextField(String(localized: "Words", comment: "Draft: the body of a mail or a message"), text: $words, axis: .vertical)
                        .lineLimit(4...20).textRole(.body).padding(Space.groupedGutter)
                }
                VStack(spacing: Space.s) {
                    Button { askingSend = true } label: {
                        Text(isMail ? String(localized: "Send with Mail", comment: "Draft: sends the mail") : String(localized: "Send with Messages", comment: "Draft: sends the message"))
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(sending || words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button { askingDiscard = true } label: {
                        Text(String(localized: "Discard", comment: "Edit sheet: throws the changes away")).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(DestructiveButtonStyle())
                    .disabled(sending)
                    Button {
                        dismiss()
                        actions.openRun(draft.runId, nil)
                    } label: {
                        Text(String(localized: "Open its chat", comment: "Draft: opens the chat of the run that wrote it")).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
        }
        .fullSheet(title: isMail ? String(localized: "Mail", comment: "Draft: an email") : String(localized: "Message", comment: "Draft: an iMessage")) {
            if dirty { askingClose = true } else { dismiss() }
        }
        .interactiveDismissDisabled(dirty || sending)
        .confirmationDialog(
            String(localized: "Send this to \(recipients) now?", comment: "Draft: asks before sending; the argument is the recipients"), isPresented: $askingSend,
            titleVisibility: .visible
        ) {
            Button(String(localized: "Send", comment: "Draft: sends it")) { Task { await send() } }
        }
        .confirmationDialog(String(localized: "Discard this draft?", comment: "Draft: asks before discarding"), isPresented: $askingDiscard, titleVisibility: .visible) {
            Button(String(localized: "Discard", comment: "Edit sheet: throws the changes away"), role: .destructive) { Task { await discard() } }
        }
        .confirmationDialog(String(localized: "Discard your changes?", comment: "Edit sheet: cancel with unsaved changes"), isPresented: $askingClose, titleVisibility: .visible) {
            Button(String(localized: "Discard", comment: "Edit sheet: throws the changes away"), role: .destructive) { dismiss() }
        }
        .onAppear {
            subject = draft.subject
            words = draft.body
            refusal = draft.status == "failed" ? draft.error : nil
        }
    }

    private func send() async {
        sending = true
        defer { sending = false }
        let sent: DraftWords = DraftWords(to: draft.to, cc: isMail ? draft.cc : [], subject: isMail ? subject : "", body: words)
        settle(await model.settleDraft { api throws(DaemonRequestError) in try await api.sendDraft(draft.id, words: sent) })
    }

    private func discard() async {
        sending = true
        defer { sending = false }
        settle(await model.settleDraft { api throws(DaemonRequestError) in try await api.discardDraft(draft.id) })
    }

    private func settle(_ outcome: DraftOutcome) {
        switch outcome {
        case .answered(let after) where after.status == "sent" || after.status == "discarded":
            settled(draft.id)
            actions.say(
                after.status == "sent"
                    ? String(localized: "Sent.", comment: "Draft: it went out") : String(localized: "Draft discarded.", comment: "Draft: it was dropped"),
                after.status == "sent" ? "paperplane" : "trash")
            dismiss()
        case .answered(let after):
            refusal = after.error ?? after.status
            problem = nil
        case .answeredElsewhere:
            settled(draft.id)
            actions.say(String(localized: "This draft was answered elsewhere.", comment: "Draft: it was sent or discarded on another screen"), "checkmark")
            dismiss()
        case .failed(let failure): problem = failure
        }
    }
}

/// A proposal of Self-improvement (docs/harness.md): what it is in a line, its habits and tips as points, whether a line
/// may come from content others can write (Use then asks first) or the checks refuse it (it cannot be used); Use puts
/// it in use for the tasks that start from now on, Set aside keeps it for the record.
struct ProposalSheet: View {
    let version: HarnessVersion
    let outside: [String]
    let refusal: String?
    let done: (String) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @Environment(\.locale) private var locale: Locale
    @State private var askingUse: Bool = false
    @State private var askingAside: Bool = false
    @State private var working: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                if let problem { FailureBanner(failure: problem, macPlace: activityMacPlace, retry: nil) }
                Text(version.summary).textRole(.headline).foregroundStyle(Tone.text).fixedSize(horizontal: false, vertical: true)
                Text(proposalLine(version, outside: !outside.isEmpty, refused: refusal != nil, locale: locale)).textRole(.footnote).foregroundStyle(Tone.secondary)
                if let refusal {
                    Notice(
                        tone: .failure,
                        message: String(localized: "Refused: \(refusal). A version like this is not put in use.", comment: "Self-improvement: the checks refuse the version; their words"),
                        dismiss: nil)
                }
                if !outside.isEmpty {
                    Notice(
                        tone: .warn,
                        message: String(
                            localized: "A line may come from content others can write: \(outside.joined(separator: "; ")). Read it before you use it.",
                            comment: "Self-improvement: why a version may come from outside content"),
                        dismiss: nil)
                }
                pointsSection(String(localized: "Habits", comment: "Self-improvement: how Dot works"), points(of: version.habits))
                pointsSection(String(localized: "Tips", comment: "Self-improvement: what this Mac and its apps want"), points(of: version.toolGuidance))
                VStack(spacing: Space.s) {
                    if refusal == nil {
                        Button {
                            if outside.isEmpty { Task { await use() } } else { askingUse = true }
                        } label: {
                            Text(String(localized: "Use", comment: "Self-improvement: puts a version in use")).frame(maxWidth: .infinity)
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(working)
                    }
                    Button { askingAside = true } label: {
                        Text(String(localized: "Set aside", comment: "Self-improvement: sets a version aside")).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(working)
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
        }
        .fullSheet(title: String(localized: "Dot's proposal", comment: "Self-improvement: the sheet of one proposal")) { dismiss() }
        .confirmationDialog(
            String(localized: "Use a version written from outside content?", comment: "Self-improvement: asks before using a version from outside content"),
            isPresented: $askingUse, titleVisibility: .visible
        ) {
            Button(String(localized: "Use it", comment: "Self-improvement and skill proposals: confirms using a version or a skill")) { Task { await use() } }
        } message: {
            Text(String(localized: "The tasks that start from now on follow it.", comment: "Self-improvement: what using a version does"))
        }
        .confirmationDialog(
            String(localized: "Set this version aside?", comment: "Self-improvement: asks before setting a version aside"), isPresented: $askingAside, titleVisibility: .visible
        ) {
            Button(String(localized: "Set aside", comment: "Self-improvement: sets a version aside")) { Task { await setAside() } }
        } message: {
            Text(String(localized: "It stays, for the record and to come back to.", comment: "Self-improvement: what setting a version aside does"))
        }
    }

    @ViewBuilder private func pointsSection(_ header: String, _ shown: [String]) -> some View {
        GroupedSection(header: header, footer: nil) {
            VStack(alignment: .leading, spacing: Space.xs) {
                if shown.isEmpty {
                    Text(String(localized: "Nothing", comment: "Self-improvement: a text of a version that is empty")).textRole(.body).foregroundStyle(Tone.tertiary)
                }
                ForEach(Array(shown.enumerated()), id: \.offset) { _, point in
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        Text(verbatim: "•").textRole(.body).foregroundStyle(Tone.secondary)
                        Text(point).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.groupedGutter)
        }
    }

    private func use() async {
        working = true
        defer { working = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.adoptHarness(version.id) }) {
        case .success:
            done(String(localized: "In use: the tasks that start from now on follow it.", comment: "Self-improvement: a version was put in use"))
            dismiss()
        case .failure(let box): problem = box.failure
        }
    }

    private func setAside() async {
        working = true
        defer { working = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.retireHarness(version.id) }) {
        case .success:
            done(String(localized: "Set aside.", comment: "Self-improvement: a version was set aside"))
            dismiss()
        case .failure(let box): problem = box.failure
        }
    }
}

extension View {
    /// The inspector's chrome (✕ leading, inline title, the sheet's ground) at full height: a draft's and a proposal's
    /// actions stand at their foot, which a middle height leaves under the fold on a small phone.
    func fullSheet(title: String, close: @escaping () -> Void) -> some View {
        NavigationStack {
            scrollContentBackground(.hidden)
                .background(Tone.sheet)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                    }
                }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .tint(Tone.text)
    }
}
