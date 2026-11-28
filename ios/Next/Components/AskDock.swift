import DotEngine
import SwiftUI
import UniformTypeIdentifiers

/// What the dock asks the screen to do.
struct AskDockActions {
    /// Answers an ask: allow or deny, with a note (a question's answer), and "always in this chat".
    let decide: (_ ask: Ask, _ approve: Bool, _ note: String, _ remember: Bool) -> Void
    /// Sends a secret to its own route; the value is held only in the field.
    let giveSecret: (_ ask: Ask, _ value: String) -> Void
    /// Sends every field of a card of several secrets together (item 2040), in the card's order.
    let giveSecrets: (_ ask: Ask, _ answers: [(name: String, value: String)]) -> Void
    /// Lets an ask that ended elsewhere go.
    let dismiss: (_ id: String) -> Void
    /// Stops the run at work; `nil` when nothing can be stopped.
    let stop: (() -> Void)?
}

/// The ask dock (plan §13 B1, §20): one bounded, collapsible panel in the composer's place with every ask waiting, a
/// count, a stable order and Previous/Next; approvals, questions, hand-offs and secrets share it. Stop stays in it
/// while the run can be stopped. The composer's draft lives on beneath it, untouched.
struct AskDock: View {
    let entries: [DockEntry]
    let actions: AskDockActions
    @Binding var folded: Bool
    @State private var current: String?
    /// What was picked and written for each question, kept while the owner pages between them (§21: one Submit).
    @State private var drafts: [String: QuestionDraft] = [:]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    private var submittable: [(ask: Ask, answer: String)] { submittedAnswers(entries, drafts: drafts) }
    /// The questions still to answer: Submit sends them together once each has an answer (item 2001).
    private var openQuestions: Int { entries.filter { $0.ask.kind == .question && $0.phase.answerable }.count }
    private var showsQuestion: Bool { entries.indices.contains(index) && entries[index].ask.kind == .question }

    private var index: Int {
        guard let current, let at = entries.firstIndex(where: { $0.id == current }) else { return 0 }
        return at
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            header
            if !folded, entries.indices.contains(index) {
                ScrollView {
                    AskCard(
                        entry: entries[index], actions: actions,
                        draft: Binding(get: { drafts[entries[index].id] ?? .empty }, set: { drafts[entries[index].id] = $0 })
                    )
                    .id(entries[index].id)
                }
                .scrollBounceBehavior(.basedOnSize)
                // The dock is bounded (B1) and scrolls inside; at the accessibility sizes it may take more of the screen.
                .frame(maxHeight: dynamicTypeSize.isAccessibilitySize ? 560 : 420)
                .fixedSize(horizontal: false, vertical: true)
            }
            if showsQuestion, !folded {
                // One Submit for every question answered (§21), ink when there is something to send.
                Button {
                    for (ask, answer) in submittable { actions.decide(ask, true, answer, false) }
                } label: {
                    Text(
                        openQuestions > 1
                            ? String(localized: "Submit answers", comment: "Ask dock: sends the answers of every question at once")
                            : String(localized: "Submit answer", comment: "Ask dock: sends the answer of the question")
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                // Every question answered first (item 2001): one Submit sends them all, none is left behind unanswered.
                .disabled(submittable.isEmpty || submittable.count < openQuestions)
                .accessibilityIdentifier("ask.submit")
            }
            if let stop = actions.stop, !folded {
                Button(action: stop) {
                    Label(String(localized: "Stop", comment: "Composer and ask dock: stops the run at work"), systemImage: "stop.fill").frame(maxWidth: .infinity)
                }
                .buttonStyle(DestructiveButtonStyle())
                    .accessibilityHint(String(localized: "Stops the run that waits for this answer", comment: "VoiceOver hint of Stop in the ask dock"))
            }
        }
        .padding(Space.m)
        .background(Tone.card, in: rounded(Radius.composer))
        .overlay(alignment: .top) { Capsule().fill(Tone.warn).frame(width: 36, height: 3).padding(.top, 5).accessibilityHidden(true) }
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: folded)
        .animation(motionAnimation(.disclosure, reduceMotion: reduceMotion), value: index)
        .onChange(of: entries.map(\.id)) { old, new in
            // A new ask unfolds the dock: something new to answer. The one being read stays the one shown.
            if new.count > old.count, Set(new).subtracting(old).isEmpty == false { folded = false }
            if let current, !new.contains(current) { self.current = new.first }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(String(localized: "Dot asked you", comment: "VoiceOver: the ask dock"))
        .accessibilityIdentifier("ask.dock")
    }

    /// The owner's shot (§21): a quiet uppercase eyebrow, then a bold title, the controls at the trailing edge.
    @ViewBuilder private var header: some View {
        let eyebrow: some View = Text(String(localized: "User input needed", comment: "Ask dock header: Dot waits for the owner's answer"))
            .textRole(.label).fontWeight(.semibold).textCase(.uppercase).tracking(1.2).foregroundStyle(Tone.tertiary)
        let title: some View = Text(
            showsQuestion
                ? String(localized: "Fill in the pending answers", comment: "Ask dock title while a question shows")
                : entries.indices.contains(index) ? askTitle(entries[index].ask) : ""
        )
        .textRole(.headline).foregroundStyle(Tone.text)
        if dynamicTypeSize.isAccessibilitySize {
            // At the accessibility sizes the words stand on their own lines, the controls under them.
            VStack(alignment: .leading, spacing: Space.xxs) {
                eyebrow
                title
                controlsRow
            }
        } else {
            HStack(alignment: .top, spacing: Space.xs) {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    eyebrow
                    if !folded { title }
                }
                Spacer(minLength: 0)
                controlsRow
            }
        }
    }

    private var controlsRow: some View {
        HStack(spacing: Space.xs) {
            if entries.count > 1 {
                Text(String(localized: "\(index + 1) / \(entries.count)", comment: "Ask dock: which ask is shown, \"1 / 3\"; the arguments are its place and the count"))
                    .textRole(.label).foregroundStyle(Tone.secondary).tabularNumbers().lineLimit(1).fixedSize()
            }
            if dynamicTypeSize.isAccessibilitySize { Spacer(minLength: 0) }
            if entries.count > 1, !folded {
                Button { move(-1) } label: { Symbol(name: "chevron.left", context: .toolbar).frame(width: Metric.hit, height: Metric.hit) }
                    .buttonStyle(.plain).disabled(index == 0)
                    .accessibilityLabel(String(localized: "Previous ask", comment: "Ask dock: shows the ask before"))
                Button { move(1) } label: { Symbol(name: "chevron.right", context: .toolbar).frame(width: Metric.hit, height: Metric.hit) }
                    .buttonStyle(.plain).disabled(index >= entries.count - 1)
                    .accessibilityLabel(String(localized: "Next ask", comment: "Ask dock: shows the ask after"))
            }
            Button { folded.toggle() } label: {
                Symbol(name: "chevron.down", context: .toolbar).rotationEffect(.degrees(folded ? 180 : 0)).frame(width: Metric.hit, height: Metric.hit)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(folded ? String(localized: "Show what Dot asked", comment: "Ask dock: unfolds it") : String(localized: "Fold what Dot asked", comment: "Ask dock: folds it to its header"))
        }
        .foregroundStyle(Tone.secondary)
    }

    private func move(_ step: Int) {
        let next: Int = min(max(0, index + step), entries.count - 1)
        current = entries[next].id
    }
}

/// One ask in the dock: its title, its words whole (never cut at a dash), why, what it acts on in a code box, and the
/// controls of its kind; its phase under them.
struct AskCard: View {
    let entry: DockEntry
    let actions: AskDockActions
    @Binding var draft: QuestionDraft
    @State private var secret: String = ""
    @FocusState private var secretFocused: Bool
    /// The values of a card of several secrets while they are typed (item 2040): in memory, emptied once sent.
    @State private var secrets: [String: String] = [:]
    /// What the card says of each file field: the file picked (its name and size) or why it was not taken.
    @State private var fileWords: [String: String] = [:]
    /// The file field whose picker is open.
    @State private var choosingFile: String?
    /// The id fields the owner chose to see as they type.
    @State private var revealed: Set<String> = []

    private var ask: Ask { entry.ask }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: Space.xs) {
                // A question's topic label (§21), else what kind of ask it is.
                Text(ask.kind == .question ? ask.topic : askTitle(ask)).textRole(.label).fontWeight(.semibold)
                    .textCase(ask.kind == .question ? .uppercase : nil).tracking(ask.kind == .question ? 1.2 : 0)
                    .foregroundStyle(ask.kind == .question ? Tone.tertiary : Tone.warn)
                if ask.fromHelper {
                    Text(String(localized: "· a helper asks", comment: "Ask dock: the ask is a helper's")).textRole(.label).foregroundStyle(Tone.secondary)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            if !ask.text.isEmpty {
                Text(ask.text).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            if !ask.why.isEmpty {
                Text(ask.why).textRole(.footnote).foregroundStyle(Tone.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if !ask.target.isEmpty {
                CodeBlock(language: String(localized: "What it acts on", comment: "Ask dock: the header of the box with the command or path"), code: ask.target)
            }
            if let words = askPhaseWords(entry.phase) { phaseLine(words) }
            if entry.phase.answerable { controls }
        }
    }

    @ViewBuilder private var controls: some View {
        switch ask.kind {
        case .approval:
            stacked(
                primary: String(localized: "Allow once", comment: "Ask dock: allows this step once"),
                approve: { actions.decide(ask, true, "", false) },
                extra: ask.rememberable
                    ? (String(localized: "Always allow in this chat", comment: "Ask dock: allows steps of this kind in this chat without asking"), { actions.decide(ask, true, "", true) })
                    : nil,
                deny: (ask.kept ? String(localized: "Skip", comment: "Ask dock: skips a question or a kept step") : String(localized: "Deny", comment: "Ask dock: refuses the step"), { actions.decide(ask, false, "", false) }), primaryEnabled: true)
        case .handoff:
            Text(String(localized: "Do this step yourself on your Mac (Dot's window comes to the front and Dot waits), then tap I did it. Taking the browser over from the phone comes in a later version.", comment: "Ask dock: what a hand-off asks of the owner"))
                .textRole(.footnote).foregroundStyle(Tone.secondary).fixedSize(horizontal: false, vertical: true)
            stacked(
                primary: String(localized: "I did it", comment: "Ask dock: says a handed-over step is done"), approve: { actions.decide(ask, true, "", false) }, extra: nil,
                deny: (String(localized: "Skip this step", comment: "Ask dock: skips a handed-over step"), { actions.decide(ask, false, "", false) }), primaryEnabled: true)
        case .question:
            question
        case .secret:
            if ask.secretFields.isEmpty { secretField } else { secretFieldsCard }
        }
    }

    /// A question as the owner's shot (§21): the full question, each choice a tonal card with its bold label (the
    /// recommended first, "(Recommended)") and its description, the field for an own answer; Submit is the dock's.
    private var question: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            if ask.multiple {
                Text(String(localized: "Choose one or more, then send them.", comment: "Ask dock: a question that takes several options")).textRole(.label).foregroundStyle(Tone.secondary)
            }
            ForEach(shownOptions(ask.options), id: \.label) { option in
                let picked: Bool = draft.chosen.contains(option.label)
                Button {
                    draft = draft.picking(option.label, multiple: ask.multiple)
                } label: {
                    // A full-width card (the owner's reference, item 2001): the bold label over its grey description,
                    // whole at every text size; the pick is an ink ring and a check, never a colour alone.
                    HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text(option.recommended ? String(localized: "\(option.label) (Recommended)", comment: "Ask dock: the option Dot recommends; the argument is its label") : option.label)
                                .textRole(.body).fontWeight(.semibold).foregroundStyle(Tone.text)
                                .fixedSize(horizontal: false, vertical: true)
                            if !option.detail.isEmpty {
                                Text(option.detail).textRole(.footnote).foregroundStyle(Tone.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        Spacer(minLength: 0)
                        if ask.multiple || picked {
                            Image(systemName: ask.multiple ? (picked ? "checkmark.square.fill" : "square") : "checkmark.circle.fill")
                                .font(.system(.body, weight: .medium))
                                .foregroundStyle(picked ? Tone.text : Tone.tertiary).accessibilityHidden(true)
                        }
                    }
                    .padding(Space.m)
                    .frame(maxWidth: .infinity, minHeight: Metric.hit, alignment: .leading)
                    .background(Tone.nested, in: rounded(Radius.inner))
                    .overlay { rounded(Radius.inner).strokeBorder(picked ? Tone.text : Color.clear, lineWidth: 1.5) }
                    .contentShape(rounded(Radius.inner))
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(picked ? .isSelected : [])
                .accessibilityIdentifier("ask.option")
            }
            TextField(
                ask.options.isEmpty ? String(localized: "Your answer", comment: "Ask dock: the field of a question's answer") : String(localized: "Or write your own answer", comment: "Ask dock: the field beside a question's options"),
                text: $draft.written, axis: .vertical
            )
            .textRole(.body).lineLimit(1...5).padding(Space.m).background(Tone.nested, in: rounded(Radius.inner))
            .accessibilityIdentifier("ask.written")
            .accessibilityLabel(String(localized: "Your answer", comment: "Ask dock: the field of a question's answer"))
            Button(String(localized: "Skip this question", comment: "Ask dock: skips one question without an answer")) { actions.decide(ask, false, "", false) }
                .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary).frame(minHeight: Metric.hit)
        }
    }

    private var secretField: some View {
        let value: String = secret.trimmingCharacters(in: .whitespacesAndNewlines)
        let short: Bool = !value.isEmpty && value.count < secretLeastCharacters
        return VStack(alignment: .leading, spacing: Space.xs) {
            SecureField(ask.text, text: $secret)
                .textContentType(.password).textInputAutocapitalization(.never).autocorrectionDisabled()
                .focused($secretFocused)
                .textRole(.body).padding(Space.s).frame(minHeight: Metric.hit).background(Tone.nested, in: rounded(Radius.inner))
                .accessibilityLabel(ask.text)
            Text(String(localized: "Sent once to your Mac, held for this chat's commands; never kept on this phone.", comment: "Ask dock: where a secret goes"))
                .textRole(.label).foregroundStyle(Tone.tertiary)
            if short {
                Text(String(localized: "A secret is at least \(secretLeastCharacters) characters, so it can be masked where a command prints it. A short code (an SMS code, a PIN) can go as a message, which Dot sees.", comment: "Ask dock: a secret that is too short; the argument is the least length"))
                    .textRole(.footnote).foregroundStyle(Tone.secondary).fixedSize(horizontal: false, vertical: true)
                Button {
                    actions.decide(ask, false, value, false)
                    secret = ""
                } label: {
                    Text(String(localized: "Send as a message", comment: "Ask dock: sends a short code in words instead of as a secret")).frame(maxWidth: .infinity)
                }
                .buttonStyle(SecondaryButtonStyle())
            }
            stacked(
                primary: String(localized: "Save securely", comment: "Ask dock: sends a secret to the Mac"),
                approve: {
                    actions.giveSecret(ask, value)
                    secret = ""
                }, extra: nil,
                deny: (String(localized: "Decline", comment: "Ask dock: refuses to give a secret"), { actions.decide(ask, false, "", false) }),
                primaryEnabled: value.count >= secretLeastCharacters)
        }
    }

    /// A card of several secrets (item 2040), as the web's: each field's words and the name its commands read it as, a box
    /// that shows no letter (a secret), one that shows them (an id the owner copies), or a key file picked on the phone
    /// (read once as text, sent, and let go); Save securely once every field holds a value the card holds.
    private var secretFieldsCard: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            ForEach(ask.secretFields) { field in
                VStack(alignment: .leading, spacing: Space.xxs) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        Text(field.label).textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.text)
                        Text(verbatim: "$" + field.name).textRole(.label).foregroundStyle(Tone.tertiary).lineLimit(1).truncationMode(.middle)
                    }
                    .accessibilityElement(children: .combine)
                    secretInput(field)
                }
            }
            Text(String(localized: "Sent once to your Mac, held for this chat's commands; never kept on this phone.", comment: "Ask dock: where a secret goes"))
                .textRole(.label).foregroundStyle(Tone.tertiary)
            stacked(
                primary: String(localized: "Save securely", comment: "Ask dock: sends a secret to the Mac"),
                approve: {
                    actions.giveSecrets(ask, ask.secretFields.map { (name: $0.name, value: secrets[$0.name] ?? "") })
                    secrets = [:]
                    fileWords = [:]
                }, extra: nil,
                deny: (String(localized: "Decline", comment: "Ask dock: refuses to give a secret"), {
                    secrets = [:]
                    fileWords = [:]
                    actions.decide(ask, false, "", false)
                }),
                primaryEnabled: secretFieldsReady(ask.secretFields, values: secrets))
        }
        .fileImporter(
            isPresented: Binding(get: { choosingFile != nil }, set: { if !$0 { choosingFile = nil } }),
            allowedContentTypes: [.data, .text], allowsMultipleSelection: false
        ) { result in
            guard let name = choosingFile, let field = ask.secretFields.first(where: { $0.name == name }) else { return }
            choosingFile = nil
            take(result, for: field)
        }
    }

    @ViewBuilder private func secretInput(_ field: SecretField) -> some View {
        let binding: Binding<String> = Binding(get: { secrets[field.name] ?? "" }, set: { secrets[field.name] = $0 })
        switch field.kind {
        case .secret:
            SecureField(field.label, text: binding)
                .textContentType(.password).textInputAutocapitalization(.never).autocorrectionDisabled()
                .textRole(.body).padding(Space.s).frame(minHeight: Metric.hit).background(Tone.nested, in: rounded(Radius.inner))
                .accessibilityLabel(field.label).accessibilityIdentifier("secret.field." + field.name)
        case .text:
            // An id's box shows no letter at first either: the run chose the kind, and an injected one could ask for a
            // password as an "id"; the owner reveals it with the eye (the security review of item 2040).
            HStack(spacing: Space.xs) {
                Group {
                    if revealed.contains(field.name) {
                        TextField(field.label, text: binding)
                    } else {
                        SecureField(field.label, text: binding)
                    }
                }
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .textRole(.body).padding(Space.s).frame(minHeight: Metric.hit).background(Tone.nested, in: rounded(Radius.inner))
                .accessibilityLabel(field.label).accessibilityIdentifier("secret.field." + field.name)
                Button {
                    if revealed.contains(field.name) { revealed.remove(field.name) } else { revealed.insert(field.name) }
                } label: {
                    Image(systemName: revealed.contains(field.name) ? "eye.slash" : "eye").frame(width: Metric.hit, height: Metric.hit)
                }
                .buttonStyle(.plain).foregroundStyle(Tone.secondary)
                .accessibilityLabel(
                    revealed.contains(field.name)
                        ? String(localized: "Hide", comment: "Ask dock: hides an id typed on a card of secrets")
                        : String(localized: "Show", comment: "Ask dock: shows an id typed on a card of secrets"))
                .accessibilityIdentifier("secret.reveal." + field.name)
            }
        case .file:
            HStack(spacing: Space.s) {
                Button {
                    choosingFile = field.name
                } label: {
                    Label(String(localized: "Choose a file", comment: "Ask dock: picks the key file a card of secrets asks for"), systemImage: "doc.badge.plus")
                }
                .buttonStyle(SecondaryButtonStyle())
                .accessibilityIdentifier("secret.file." + field.name)
                Text(fileWords[field.name] ?? String(localized: "No file chosen", comment: "Ask dock: a card's file field before a file is picked"))
                    .textRole(.label).foregroundStyle(Tone.secondary).lineLimit(2)
            }
        }
    }

    /// Reads the key file the owner picked for `field`, once, as text: taken when it is text and no longer than the field
    /// holds; else the card says why and the field stays empty. The file's access is let go at once.
    private func take(_ result: Result<[URL], any Error>, for field: SecretField) {
        secrets[field.name] = nil
        guard case .success(let urls) = result, let url = urls.first else { return }
        let opened: Bool = url.startAccessingSecurityScopedResource()
        defer { if opened { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else {
            fileWords[field.name] = String(localized: "\(url.lastPathComponent) could not be read", comment: "Ask dock: a picked key file that could not be read; the argument is its name")
            return
        }
        guard data.count <= field.most else {
            fileWords[field.name] = String(localized: "\(url.lastPathComponent) is too large for a card: a key file is a few KB at most.", comment: "Ask dock: a picked file larger than a card's field holds; the argument is its name")
            return
        }
        // A key in PEM alone (the daemon refuses any other): a JSON or an .env holds its secret in a form no mask reaches.
        guard let text = String(data: data, encoding: .utf8), !text.contains("\u{0}"), text.contains("-----BEGIN ") else {
            fileWords[field.name] = String(localized: "\(url.lastPathComponent) is not a key in PEM: pick the key file itself (.p8, .pem, .key).", comment: "Ask dock: a picked file that is no key in PEM; the argument is its name")
            return
        }
        secrets[field.name] = text
        fileWords[field.name] = String(localized: "\(url.lastPathComponent) · \(data.count) bytes", comment: "Ask dock: the key file picked; the arguments are its name and size")
    }

    /// The buttons stacked full width (thumb reach, R-c #7): the yes in ink, then the others.
    private func stacked(primary: String, approve: @escaping () -> Void, extra: (String, () -> Void)?, deny: (String, () -> Void), primaryEnabled: Bool) -> some View {
        VStack(spacing: Space.xs) {
            Button(action: approve) { Text(primary).frame(maxWidth: .infinity) }.buttonStyle(PrimaryButtonStyle()).disabled(!primaryEnabled)
                .accessibilityIdentifier("ask.primary")
            if let extra { Button(action: extra.1) { Text(extra.0).frame(maxWidth: .infinity) }.buttonStyle(SecondaryButtonStyle()) }
            Button(action: deny.1) { Text(deny.0).frame(maxWidth: .infinity) }.buttonStyle(SecondaryButtonStyle())
        }
    }

    @ViewBuilder private func phaseLine(_ words: String) -> some View {
        HStack(spacing: Space.xs) {
            if entry.phase == .submitting { ProgressView().controlSize(.small) }
            Text(words).textRole(.footnote).foregroundStyle(isFailure ? Tone.failure : Tone.secondary).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            switch entry.phase {
            case .answeredElsewhere, .expired, .refused:
                Button(String(localized: "Dismiss", comment: "Ask dock: lets an ask that ended go")) { actions.dismiss(ask.id) }.textRole(.footnote).fontWeight(.medium)
                    .frame(minHeight: Metric.hit)
            default: EmptyView()
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var isFailure: Bool {
        switch entry.phase {
        case .refused, .retry: true
        default: false
        }
    }
}
