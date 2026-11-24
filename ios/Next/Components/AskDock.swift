import DotEngine
import SwiftUI

/// What the dock asks the screen to do.
struct AskDockActions {
    /// Answers an ask: allow or deny, with a note (a question's answer), and "always in this chat".
    let decide: (_ ask: Ask, _ approve: Bool, _ note: String, _ remember: Bool) -> Void
    /// Sends a secret to its own route; the value is held only in the field.
    let giveSecret: (_ ask: Ask, _ value: String) -> Void
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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    private var index: Int {
        guard let current, let at = entries.firstIndex(where: { $0.id == current }) else { return 0 }
        return at
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            header
            if !folded, entries.indices.contains(index) {
                ScrollView {
                    AskCard(entry: entries[index], actions: actions).id(entries[index].id)
                }
                .scrollBounceBehavior(.basedOnSize)
                // The dock is bounded (B1) and scrolls inside; at the accessibility sizes it may take more of the screen.
                .frame(maxHeight: dynamicTypeSize.isAccessibilitySize ? 560 : 420)
                .fixedSize(horizontal: false, vertical: true)
            }
            if let stop = actions.stop, !folded {
                Button(action: stop) {
                    Label(String(localized: "Stop", comment: "Chat toolbar: stops the run at work"), systemImage: "stop.fill").frame(maxWidth: .infinity)
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

    @ViewBuilder private var header: some View {
        if dynamicTypeSize.isAccessibilitySize {
            // At the accessibility sizes the title stands on its own line, the controls under it.
            VStack(alignment: .leading, spacing: 0) {
                Text(String(localized: "Your answer is needed", comment: "Ask dock header")).textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.text)
                controlsRow(showsTitle: false)
            }
        } else {
            controlsRow(showsTitle: true)
        }
    }

    private func controlsRow(showsTitle: Bool) -> some View {
        HStack(spacing: Space.xs) {
            if showsTitle {
                Image(systemName: "exclamationmark.bubble").foregroundStyle(Tone.warn).accessibilityHidden(true)
                Text(String(localized: "Your answer is needed", comment: "Ask dock header")).textRole(.footnote).fontWeight(.semibold).foregroundStyle(Tone.text)
            }
            if entries.count > 1 {
                Text(String(localized: "\(index + 1) of \(entries.count)", comment: "Ask dock: which ask is shown; the arguments are its place and the count"))
                    .textRole(.label).foregroundStyle(Tone.secondary).tabularNumbers()
            }
            Spacer(minLength: 0)
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
    @State private var written: String = ""
    @State private var chosen: Set<String> = []
    @State private var secret: String = ""
    @FocusState private var secretFocused: Bool

    private var ask: Ask { entry.ask }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: Space.xs) {
                Text(askTitle(ask)).textRole(.label).fontWeight(.semibold).foregroundStyle(Tone.warn)
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
            secretField
        }
    }

    private var question: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            if ask.multiple {
                Text(String(localized: "Choose one or more, then send them.", comment: "Ask dock: a question that takes several options")).textRole(.label).foregroundStyle(Tone.secondary)
            }
            ForEach(ask.options, id: \.label) { option in
                Button {
                    if ask.multiple {
                        if chosen.contains(option.label) { chosen.remove(option.label) } else { chosen.insert(option.label) }
                    } else {
                        actions.decide(ask, true, chosenAnswer(chosen: [option.label], written: ""), false)
                    }
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        if ask.multiple {
                            Image(systemName: chosen.contains(option.label) ? "checkmark.square.fill" : "square").foregroundStyle(Tone.text).accessibilityHidden(true)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: Space.xs) {
                                Text(option.label).textRole(.body).foregroundStyle(Tone.text)
                                if option.recommended {
                                    Text(String(localized: "Recommended", comment: "Ask dock: the option Dot recommends")).textRole(.micro).fontWeight(.semibold)
                                        .foregroundStyle(Tone.secondary).padding(.horizontal, Space.xs).padding(.vertical, 2).background(Tone.nested, in: Capsule())
                                }
                            }
                            if !option.detail.isEmpty { Text(option.detail).textRole(.footnote).foregroundStyle(Tone.secondary) }
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(Space.s)
                    .frame(minHeight: Metric.hit)
                    .background(Tone.nested, in: rounded(Radius.inner))
                    .contentShape(rounded(Radius.inner))
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(ask.multiple && chosen.contains(option.label) ? .isSelected : [])
            }
            TextField(
                ask.options.isEmpty ? String(localized: "Your answer", comment: "Ask dock: the field of a question's answer") : String(localized: "Or write your own answer", comment: "Ask dock: the field beside a question's options"),
                text: $written, axis: .vertical
            )
            .textRole(.body).lineLimit(1...5).padding(Space.s).background(Tone.nested, in: rounded(Radius.inner))
            .accessibilityLabel(String(localized: "Your answer", comment: "Ask dock: the field of a question's answer"))
            let answer: String = chosenAnswer(chosen: ask.options.map(\.label).filter { chosen.contains($0) }, written: written)
            stacked(
                primary: String(localized: "Send answer", comment: "Ask dock: sends the answer to a question"), approve: { actions.decide(ask, true, answer, false) }, extra: nil,
                deny: (String(localized: "Skip", comment: "Ask dock: skips a question or a kept step"), { actions.decide(ask, false, "", false) }), primaryEnabled: !answer.isEmpty)
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
