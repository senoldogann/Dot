import DotEngine
import SwiftUI

/// The drafts the chat's runs wrote for the owner (web `drafts.js`, item 314): an email or a message that goes out only
/// when the owner sends it. Each open draft is a card with its recipients, subject and words, Send (asked first, as the
/// owner's word goes out), Edit (a sheet; Send from there sends the edited words) and Discard (asked first); a decided
/// draft is one quiet line. A card answered elsewhere (409) reads the draft again and says so. An email drafted in the
/// owner's own Gmail or Outlook (item 2047) names the account it goes from and opens there too.
struct ChatDrafts: View {
    let thread: String
    @Environment(AppModel.self) private var model: AppModel

    var body: some View {
        let drafts: [DraftRecord] = model.chats[thread]?.view?.drafts ?? []
        ForEach(drafts, id: \.id) { record in
            DraftCard(record: record)
        }
    }
}

struct DraftCard: View {
    let record: DraftRecord
    @Environment(AppModel.self) private var model: AppModel
    @State private var draft: DraftView?
    /// The owner's edit of the words, kept on the card until it is sent (the daemon keeps no edit of a draft).
    @State private var edited: DraftWords?
    @State private var failure: String?
    @State private var busy: Bool = false
    @State private var editing: Bool = false
    @State private var confirmingSend: Bool = false
    @State private var confirmingDiscard: Bool = false
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    private var buttonsLayout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.xs)) : AnyLayout(HStackLayout(spacing: Space.xs))
    }

    private var status: String { draft?.status ?? record.status }
    private var isOpen: Bool { status == "waiting" || status == "failed" }
    private var mail: Bool { record.channel == "mail" }

    var body: some View {
        Group {
            if isOpen { card } else { decidedLine }
        }
        // The whole record: a run that writes the draft again changes its words while it still waits (item 2047).
        .task(id: record) { await load() }
        .sheet(isPresented: $editing) {
            if let draft {
                DraftEditor(draft: draft, words: words(of: draft), close: { editing = false }, save: { words in
                    editing = false
                    edited = words
                })
            }
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack(spacing: Space.xs) {
                Symbol(name: mail ? "envelope" : "bubble.left", context: .tool).foregroundStyle(Tone.secondary)
                Text(mail ? String(localized: "Email draft", comment: "Draft card eyebrow: an email Dot wrote") : String(localized: "Message draft", comment: "Draft card eyebrow: a message Dot wrote"))
                    .textRole(.label).fontWeight(.medium).foregroundStyle(Tone.secondary)
                Spacer(minLength: 0)
                Text(stateWord).textRole(.label).foregroundStyle(status == "failed" ? Tone.failure : Tone.warn)
            }
            if let draft {
                let shown: DraftWords = words(of: draft)
                if let mailbox = draft.mailbox {
                    field(String(localized: "Goes from", comment: "Draft card: the account of the owner's the email goes from"), "\(mailbox.account) (\(mailbox.name))")
                }
                field(String(localized: "To", comment: "Draft card: the recipients"), shown.to.joined(separator: ", "))
                if !shown.cc.isEmpty { field(String(localized: "Cc", comment: "Draft card: the copied recipients"), shown.cc.joined(separator: ", ")) }
                if !shown.subject.isEmpty { field(String(localized: "Subject", comment: "Draft card: the email's subject"), shown.subject) }
                Text(shown.body).textRole(.body).foregroundStyle(Tone.text).lineLimit(8).textSelection(.enabled)
                if edited != nil {
                    Text(String(localized: "Edited on this phone: Send sends these words.", comment: "Draft card: the owner changed the words here"))
                        .textRole(.label).foregroundStyle(Tone.tertiary)
                }
                if let error = draft.error, status == "failed" {
                    Text(String(localized: "Not sent: \(error)", comment: "Draft card: why the last send failed; the argument is the application's words"))
                        .textRole(.footnote).foregroundStyle(Tone.failure)
                }
            } else {
                Text(record.subject).textRole(.body).foregroundStyle(Tone.text)
            }
            if let failure { Text(failure).textRole(.footnote).foregroundStyle(Tone.failure) }
            // A line of its own, so the row of buttons stays three wide on the narrowest phone.
            if let mailbox = draft?.mailbox, let place = URL(string: mailbox.link), place.scheme == "https" {
                Link(destination: place) {
                    Label(mailbox.provider == "google" ? String(localized: "Open in Gmail", comment: "Draft card: opens the draft in the owner's Gmail") : String(localized: "Open in Outlook", comment: "Draft card: opens the draft in the owner's Outlook"), systemImage: "arrow.up.right.square")
                        .textRole(.footnote)
                }
                .accessibilityIdentifier("draft.open")
            }
            // At the accessibility sizes the buttons stand one under the other, none cut.
            buttonsLayout {
                Button(mail ? String(localized: "Send email", comment: "Draft card: sends the email") : String(localized: "Send message", comment: "Draft card: sends the message")) {
                    confirmingSend = true
                }
                .buttonStyle(PrimaryButtonStyle()).accessibilityIdentifier("draft.send")
                Button(String(localized: "Edit", comment: "Draft card: opens the draft to change it before sending")) { editing = true }
                    .buttonStyle(SecondaryButtonStyle()).accessibilityIdentifier("draft.edit")
                Button(String(localized: "Discard", comment: "Draft card: drops the draft")) { confirmingDiscard = true }
                    .buttonStyle(SecondaryButtonStyle()).accessibilityIdentifier("draft.discard")
                if busy { ProgressView().controlSize(.small) }
            }
            .disabled(busy || draft == nil)
        }
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Tone.nested, in: rounded(Radius.bubble))
        .confirmationDialog(
            mail ? String(localized: "Send this email?", comment: "Confirmation of a draft's Send (email)") : String(localized: "Send this message?", comment: "Confirmation of a draft's Send (message)"),
            isPresented: $confirmingSend, titleVisibility: .visible
        ) {
            Button(mail ? String(localized: "Send email", comment: "Draft card: sends the email") : String(localized: "Send message", comment: "Draft card: sends the message")) {
                if let draft { send(words(of: draft)) }
            }
        } message: {
            if let mailbox = draft?.mailbox {
                Text(String(localized: "It goes out from \(mailbox.account) as it stands on the card.", comment: "Confirmation of a draft's Send from the owner's Gmail or Outlook; the argument is the account"))
            } else {
                Text(String(localized: "It goes out from your Mac as it stands on the card.", comment: "Confirmation of a draft's Send: what happens"))
            }
        }
        .confirmationDialog(
            String(localized: "Discard this draft?", comment: "Confirmation of a draft's Discard"), isPresented: $confirmingDiscard, titleVisibility: .visible
        ) {
            Button(String(localized: "Discard", comment: "Draft card: drops the draft"), role: .destructive) { discard() }
        }
    }

    private var decidedLine: some View {
        Label {
            Text([stateWord, draft?.subject ?? record.subject].filter { !$0.isEmpty }.joined(separator: " · ")).textRole(.footnote).foregroundStyle(Tone.secondary)
        } icon: {
            Symbol(name: mail ? "envelope" : "bubble.left", context: .tool).foregroundStyle(Tone.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func words(of draft: DraftView) -> DraftWords {
        edited ?? DraftWords(to: draft.to, cc: draft.cc, subject: draft.subject, body: draft.body)
    }

    private func field(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
            Text(label).textRole(.footnote).foregroundStyle(Tone.secondary).frame(minWidth: 56, alignment: .leading)
            Text(value).textRole(.footnote).foregroundStyle(Tone.text)
        }
        .accessibilityElement(children: .combine)
    }

    private var stateWord: String {
        switch status {
        case "waiting": String(localized: "Draft", comment: "Draft card state: waits for the owner")
        case "sending": String(localized: "Sending…", comment: "Draft card state: on its way")
        case "sent": String(localized: "Sent", comment: "Draft card state: it went out")
        case "discarded": String(localized: "Discarded", comment: "Draft card state: the owner dropped it")
        default: String(localized: "Not sent", comment: "Draft card state: the last send failed")
        }
    }

    private var macPlace: String { String(localized: "the chat on the web", comment: "Where on the Mac a chat's action is done") }

    private func load() async {
        switch await model.draft(record.id) {
        case .loaded(let read): draft = read
        case .failed(let reason): failure = failureWords(reason, macPlace: macPlace)
        case .loading: break
        }
    }

    private func send(_ words: DraftWords) {
        busy = true
        failure = nil
        Task {
            let outcome: Result<DraftView, SettingsFailureBox> = await model.sendDraft(record.id, words: words)
            busy = false
            settle(outcome)
        }
    }

    private func discard() {
        busy = true
        failure = nil
        Task {
            let outcome: Result<DraftView, SettingsFailureBox> = await model.discardDraft(record.id)
            busy = false
            settle(outcome)
        }
    }

    private func settle(_ outcome: Result<DraftView, SettingsFailureBox>) {
        switch outcome {
        case .success(let decided):
            draft = decided
            if !decided.isOpen { edited = nil }
        case .failure(let box):
            // A draft answered elsewhere meanwhile (409) is read again, so the card shows how it stands.
            failure = failureWords(box.failure, macPlace: macPlace)
            Task { await load() }
        }
    }
}

/// A draft's words to change before sending: recipients (separated by commas), copy, subject, body. Save keeps them on
/// the card, whose Send sends them; Cancel drops the edit (asked while it holds changes, as every edit sheet).
struct DraftEditor: View {
    let draft: DraftView
    let words: DraftWords
    let close: () -> Void
    let save: (DraftWords) -> Void
    @State private var to: String = ""
    @State private var cc: String = ""
    @State private var subject: String = ""
    @State private var text: String = ""

    var body: some View {
        Form {
            Section(String(localized: "To", comment: "Draft card: the recipients")) {
                TextField(String(localized: "Recipients, separated by commas", comment: "Draft editor: the recipients field"), text: $to)
                    .textInputAutocapitalization(.never).keyboardType(draft.channel == "mail" ? .emailAddress : .default)
            }
            if draft.channel == "mail" {
                Section(String(localized: "Cc", comment: "Draft card: the copied recipients")) {
                    TextField(String(localized: "Recipients, separated by commas", comment: "Draft editor: the recipients field"), text: $cc).textInputAutocapitalization(.never)
                }
                Section(String(localized: "Subject", comment: "Draft card: the email's subject")) { TextField(String(localized: "Subject", comment: "Draft card: the email's subject"), text: $subject) }
            }
            Section(String(localized: "Words", comment: "Draft editor: the body field")) {
                TextEditor(text: $text).frame(minHeight: 180).accessibilityIdentifier("draft.body")
            }
        }
        .onAppear {
            to = words.to.joined(separator: ", ")
            cc = words.cc.joined(separator: ", ")
            subject = words.subject
            text = words.body
        }
        .editSheet(
            title: String(localized: "Edit draft", comment: "Title of the draft editor"),
            isDirty: typed != words && !typed.to.isEmpty && !typed.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            cancel: close,
            confirm: { save(typed) })
    }

    private var typed: DraftWords {
        DraftWords(to: addresses(to), cc: addresses(cc), subject: subject, body: text)
    }
}

/// The addresses in what the owner typed: separated by commas, semicolons or lines (web `drafts.js`).
func addresses(_ typed: String) -> [String] {
    typed.split(whereSeparator: { $0 == "," || $0 == ";" || $0.isNewline }).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
}
