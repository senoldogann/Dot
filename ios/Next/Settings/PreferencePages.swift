import DotEngine
import SwiftUI
import UIKit

// MARK: General

/// Settings › General (the reference's General with Dot's parts): the names, the language, the clock, what a message
/// written while Dot works does, the confirmations, and dictation.
struct GeneralPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @AppStorage(PrefKey.timestampFormat) private var timestampRaw: String = TimestampFormat.system.rawValue
    @AppStorage(PrefKey.followUp) private var followUpRaw: String = FollowUp.queue.rawValue
    @AppStorage(PrefKey.confirmDelete) private var confirmDelete: Bool = true
    @AppStorage(PrefKey.dictationLanguage) private var dictationRaw: String = DictationLanguage.app.rawValue
    @State private var assistant: Loadable<AssistantView> = .loading
    @State private var dotName: String = ""
    @State private var ownerName: String = ""
    @State private var problem: SettingsFailure?
    @State private var saving: Bool = false
    @State private var appLanguage: AppLanguage = AppLanguage.chosen(.standard)
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "General", comment: "Settings row")) {
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › General", comment: "Where on the Mac: the General page"), retry: nil) }
            LoadedPage(state: assistant, macPlace: String(localized: "Settings › General", comment: "Where on the Mac: the General page"), retry: { Task { await load() } }) { _ in
                GroupedSection(
                    header: String(localized: "Names", comment: "General section: Dot's and the owner's names"),
                    footer: String(localized: "Dot uses these names in chats, on the Mac and in messages it sends for you.", comment: "General: footer under the names")
                ) {
                    nameField(String(localized: "Dot's name", comment: "General: the assistant's shown name"), text: $dotName)
                    nameField(String(localized: "Your name", comment: "General: the owner's name"), text: $ownerName)
                }
            }
            GroupedSection(
                header: String(localized: "Language", comment: "General section: the app's language"),
                footer: appLanguage.isSpoken(running: Bundle.main.preferredLocalizations.first ?? "")
                    ? String(localized: "Dot Next speaks this language whatever the iPhone's, as the web's own choice does.", comment: "General: footer under the language")
                    : String(localized: "Dot Next opens in it the next time it starts.", comment: "General: footer once another language is chosen")
            ) {
                ForEach(AppLanguage.allCases, id: \.self) { language in
                    RadioRow(title: language.title, description: nil, isSelected: appLanguage == language) {
                        appLanguage = language
                        language.keep(.standard)
                    }
                    .accessibilityIdentifier("general.language.\(language.rawValue)")
                }
            }
            GroupedSection(header: String(localized: "Timestamps", comment: "General section: how clocks read"), footer: nil) {
                ForEach(TimestampFormat.allCases, id: \.self) { format in
                    RadioRow(title: format.title, description: clockWords(sampleTime, format: format, locale: locale), isSelected: timestampRaw == format.rawValue) { timestampRaw = format.rawValue }
                }
            }
            GroupedSection(
                header: String(localized: "Follow-up while Dot works", comment: "Settings section: what a message written during a run does"),
                footer: String(localized: "The other way is always on a long press of the button.", comment: "Footer of the follow-up setting")
            ) {
                RadioRow(
                    title: String(localized: "Queue", comment: "Follow-up setting: queued after the run"),
                    description: String(localized: "Starts after the run at work", comment: "Follow-up setting: what Queue does"), isSelected: followUpRaw == FollowUp.queue.rawValue
                ) { followUpRaw = FollowUp.queue.rawValue }
                RadioRow(
                    title: String(localized: "Steer", comment: "Follow-up setting: sent into the run"),
                    description: String(localized: "Goes into the running task at its next step", comment: "Follow-up setting: what Steer does"), isSelected: followUpRaw == FollowUp.steer.rawValue
                ) { followUpRaw = FollowUp.steer.rawValue }
            }
            GroupedSection(header: String(localized: "Confirmations", comment: "General section: which actions ask first"), footer: nil) {
                ToggleRow(systemImage: "trash", title: String(localized: "Ask before deleting a chat", comment: "General: confirm deleting a chat"), description: nil, isOn: $confirmDelete)
            }
            GroupedSection(
                header: String(localized: "Voice", comment: "General section: dictation"),
                footer: String(localized: "Dictation writes your words on this iPhone; no audio leaves it.", comment: "General: footer under dictation")
            ) {
                ForEach(DictationLanguage.allCases, id: \.self) { language in
                    RadioRow(title: language.title, description: nil, isSelected: dictationRaw == language.rawValue) { dictationRaw = language.rawValue }
                }
            }
        }
        .task { await load() }
    }

    private var sampleTime: Date { Calendar.current.date(bySettingHour: 14, minute: 5, second: 0, of: .now) ?? .now }


    private func nameField(_ title: String, text: Binding<String>) -> some View {
        HStack(spacing: Space.s) {
            Text(title).textRole(.body).foregroundStyle(Tone.text)
            Spacer(minLength: Space.s)
            TextField(title, text: text)
                .multilineTextAlignment(.trailing)
                .textRole(.body).foregroundStyle(Tone.secondary)
                .submitLabel(.done)
                .onSubmit { Task { await save() } }
                .disabled(saving)
        }
        .padding(.horizontal, Space.groupedGutter)
        .padding(.vertical, Space.s)
        .frame(minHeight: Metric.row)
    }

    private func load() async {
        assistant = await model.read { api throws(DaemonRequestError) in try await api.assistant() }
        if let value = assistant.value {
            dotName = value.name
            ownerName = value.owner
        }
    }

    private func save() async {
        guard let before = assistant.value else { return }
        let change: AssistantChange = AssistantChange(name: dotName.trimmingCharacters(in: .whitespaces), owner: ownerName.trimmingCharacters(in: .whitespaces))
        guard change.name != before.name || change.owner != before.owner else { return }
        saving = true
        switch await model.write({ api throws(DaemonRequestError) in try await api.changeAssistant(change) }) {
        case .success(let saved):
            assistant = .loaded(saved)
            problem = nil
        case .failure(let box):
            dotName = before.name
            ownerName = before.owner
            problem = box.failure
        }
        saving = false
    }
}

// MARK: Appearance

/// Settings › Appearance (the owner's reference shots): the look as three previews, the text size with a live
/// preview, the tool calls in chat (four modes, D9), response streaming, the reading width on a wide screen.
struct AppearancePage: View {
    @AppStorage(PrefKey.colorScheme) private var schemeRaw: String = ColorSchemeChoice.system.rawValue
    @AppStorage(PrefKey.textSize) private var sizeRaw: String = TextSizeChoice.system.rawValue
    @AppStorage(PrefKey.toolCallMode) private var toolModeRaw: String = DotEngine.ToolCallMode.hideAll.rawValue
    @AppStorage(PrefKey.responseStreaming) private var streaming: Bool = true
    @AppStorage(PrefKey.readingWidth) private var widthRaw: String = ReadingWidth.standard.rawValue
    @AppStorage(PrefKey.terminalFontSize) private var terminalSize: Double = 0
    @AppStorage(PrefKey.codeFontSize) private var codeSize: Double = 0
    @AppStorage(PrefKey.codeWordBreak) private var wordBreak: Bool = true
    @Environment(\.dynamicTypeSize) private var systemSize: DynamicTypeSize

    var body: some View {
        SettingsScroll(title: String(localized: "Appearance", comment: "Settings row")) {
            VStack(alignment: .leading, spacing: Space.xs) {
                sectionLabel(String(localized: "Color scheme", comment: "Appearance section: light, dark or system"))
                HStack(spacing: Space.s) {
                    ForEach(ColorSchemeChoice.allCases, id: \.self) { choice in schemeCard(choice) }
                }
            }
            textSection
            terminalSection
            codeSection
            GroupedSection(
                header: String(localized: "Tool calls in chat", comment: "Settings section: how much of the work log a chat shows"),
                footer: String(
                    localized: "Hide the commands, edits, and searches agents run. Hide successful keeps failures. Zen shows only final answers. Approvals, questions, and errors always show. With Hide all and Zen, one line under the last message says what Dot is doing; tap it for the steps.",
                    comment: "Footer of the tool calls setting (UI-STANDARD 14:25, item 1769)")
            ) {
                ForEach(DotEngine.ToolCallMode.allCases, id: \.self) { mode in
                    RadioRow(title: mode.title, description: nil, isSelected: toolModeRaw == mode.rawValue) { toolModeRaw = mode.rawValue }
                }
            }
            GroupedSection(header: String(localized: "Answers", comment: "Appearance section: how answers appear"), footer: nil) {
                ToggleRow(
                    systemImage: "text.word.spacing", title: String(localized: "Response streaming", comment: "Settings switch: answers appear word by word as written"),
                    description: String(localized: "On: words appear as Dot writes them. Off: an answer appears once it is whole.", comment: "Description of the response streaming switch"),
                    isOn: $streaming)
            }
            GroupedSection(
                header: String(localized: "Reading width", comment: "Appearance section: how wide prose runs on iPad"),
                footer: String(localized: "On iPad and other wide windows; a phone uses its whole width.", comment: "Appearance: footer of the reading width")
            ) {
                ForEach(ReadingWidth.allCases, id: \.self) { width in
                    RadioRow(
                        title: width.title, description: String(localized: "\(width.charactersPerLine) characters a line", comment: "Appearance: a reading width's measure"),
                        isSelected: widthRaw == width.rawValue
                    ) { widthRaw = width.rawValue }
                }
            }
        }
    }

    private func sectionLabel(_ text: String) -> some View {
        Text(text).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary).padding(.horizontal, Space.xs).accessibilityAddTraits(.isHeader)
    }

    /// A look as the reference draws it: a small phone in that look, its name under it; the chosen one ringed in the ink.
    private func schemeCard(_ choice: ColorSchemeChoice) -> some View {
        let selected: Bool = schemeRaw == choice.rawValue
        return Button {
            schemeRaw = choice.rawValue
        } label: {
            VStack(spacing: Space.s) {
                SchemePreview(choice: choice).frame(width: 52, height: 84)
                Text(choice.title).textRole(.footnote).fontWeight(selected ? .semibold : .regular).foregroundStyle(selected ? Tone.text : Tone.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, Space.m)
            .background(Tone.card, in: rounded(Radius.section))
            .overlay {
                if selected { rounded(Radius.section).strokeBorder(Tone.text, lineWidth: 2) }
            }
            .contentShape(rounded(Radius.section))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(choice.title)
        .accessibilityAddTraits(selected ? [.isSelected, .isButton] : .isButton)
    }

    private var size: TextSizeChoice { TextSizeChoice(rawValue: sizeRaw) ?? .system }

    /// What a command said, as a tool's detail shows it (item 1769, after another coding agent's Appearance): a preview, and a size of its
    /// own instead of the text size's.
    private var terminalSection: some View {
        GroupedSection(header: String(localized: "Terminal", comment: "Appearance section: how a command's output is drawn"), footer: nil) {
            Text(verbatim: "$ swift test\nBuilding for debugging...\nTest Suite 'All tests' passed\n  Executed 128 tests, with 0 failures (0.42 s)")
                .textRole(.code).modifier(MonoSize(points: terminalSize)).foregroundStyle(Tone.secondary)
                .frame(maxWidth: .infinity, alignment: .leading).padding(Space.s)
                .background(Tone.nested, in: rounded(Radius.inner))
                .padding(Space.groupedGutter)
                .accessibilityLabel(String(localized: "Preview of a command's output", comment: "VoiceOver: the terminal preview in Appearance"))
            customSize(systemImage: "apple.terminal", points: $terminalSize)
        }
    }

    /// Code and diffs in answers (item 1769): a preview, a size of their own, and whether a long line wraps.
    private var codeSection: some View {
        GroupedSection(header: String(localized: "Code and diffs", comment: "Appearance section: how code blocks are drawn"), footer: nil) {
            CodeBlock(language: "swift", code: "func greet(_ user: User) -> String {\n    \"Hello, \\(user.name) <\\(user.email)>\" // shows how a long line behaves\n}")
                .padding(Space.groupedGutter)
            customSize(systemImage: "chevron.left.forwardslash.chevron.right", points: $codeSize)
            ToggleRow(
                systemImage: "text.alignleft", title: String(localized: "Word break", comment: "Appearance: long lines of code wrap"),
                description: String(localized: "On: a long line wraps. Off: it scrolls sideways.", comment: "Appearance: what word break does"), isOn: $wordBreak)
        }
    }

    /// A switch for a monospaced size of its own and, when on, its slider in points (10 to 18).
    @ViewBuilder private func customSize(systemImage: String, points: Binding<Double>) -> some View {
        ToggleRow(
            systemImage: systemImage, title: String(localized: "Custom font size", comment: "Appearance: a monospaced size of its own"), description: nil,
            isOn: Binding(get: { points.wrappedValue > 0 }, set: { points.wrappedValue = $0 ? 13 : 0 }))
        if points.wrappedValue > 0 {
            HStack(spacing: Space.s) {
                Text(verbatim: "A").font(.system(size: 12, design: .monospaced)).foregroundStyle(Tone.secondary).accessibilityHidden(true)
                Slider(value: points, in: 10...18, step: 1)
                    .tint(Tone.text)
                    .accessibilityLabel(String(localized: "Custom font size", comment: "Appearance: a monospaced size of its own"))
                    .accessibilityValue(String(localized: "\(Int(points.wrappedValue)) pt", comment: "Appearance: the body text's size in points"))
                Text(verbatim: "A").font(.system(size: 19, design: .monospaced)).foregroundStyle(Tone.secondary).accessibilityHidden(true)
                Text(String(localized: "\(Int(points.wrappedValue)) pt", comment: "Appearance: the body text's size in points")).textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers()
            }
            .padding(.horizontal, Space.groupedGutter)
            .padding(.vertical, Space.s)
        }
    }

    private var textSection: some View {
        let followsPhone: Binding<Bool> = Binding(get: { size == .system }, set: { sizeRaw = $0 ? TextSizeChoice.system.rawValue : TextSizeChoice.large.rawValue })
        let shown: TextSizeChoice = size == .system ? .large : size
        let index: Binding<Double> = Binding(
            get: { Double(TextSizeChoice.steps.firstIndex(of: shown) ?? 3) },
            set: { sizeRaw = TextSizeChoice.steps[Int($0.rounded())].rawValue })
        return GroupedSection(header: String(localized: "Text", comment: "Appearance section: the text size"), footer: nil) {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(String(localized: "The quick brown fox jumps over the lazy dog.", comment: "Appearance: the text size's live preview sentence"))
                    .textRole(.body).foregroundStyle(Tone.text)
                Text(String(localized: "Messages, labels, and headings scale with this size.", comment: "Appearance: under the preview sentence"))
                    .textRole(.footnote).foregroundStyle(Tone.secondary)
            }
            .dynamicTypeSize(size.dynamicTypeSize ?? systemSize)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.groupedGutter)
            .animation(.default, value: sizeRaw)
            ToggleRow(systemImage: "iphone", title: String(localized: "Use the iPhone's text size", comment: "Appearance: follow Dynamic Type"), description: nil, isOn: followsPhone)
            if size != .system {
                VStack(spacing: Space.xs) {
                    HStack {
                        Symbol(name: "textformat.size", context: .row).foregroundStyle(Tone.text).frame(minWidth: Metric.iconSlot)
                        Text(String(localized: "Text size", comment: "Appearance: the text size slider")).textRole(.body).foregroundStyle(Tone.text)
                        Spacer()
                        Text(String(localized: "\(shown.bodyPoints) pt", comment: "Appearance: the body text's size in points")).textRole(.footnote).foregroundStyle(Tone.secondary).tabularNumbers()
                    }
                    HStack(spacing: Space.s) {
                        Text(verbatim: "A").font(.system(size: 13)).foregroundStyle(Tone.secondary).accessibilityHidden(true)
                        Slider(value: index, in: 0...Double(TextSizeChoice.steps.count - 1), step: 1)
                            .tint(Tone.text)
                            .accessibilityLabel(String(localized: "Text size", comment: "Appearance: the text size slider"))
                            .accessibilityValue(String(localized: "\(shown.bodyPoints) pt", comment: "Appearance: the body text's size in points"))
                        Text(verbatim: "A").font(.system(size: 22)).foregroundStyle(Tone.secondary).accessibilityHidden(true)
                    }
                }
                .padding(.horizontal, Space.groupedGutter)
                .padding(.vertical, Space.s)
            }
        }
    }
}

/// A tiny phone in a look: the system one split down the middle.
struct SchemePreview: View {
    let choice: ColorSchemeChoice

    var body: some View {
        switch choice {
        case .light: phone(Palette.light)
        case .dark: phone(Palette.dark)
        case .system:
            ZStack {
                phone(Palette.light)
                phone(Palette.dark).mask(alignment: .trailing) { Rectangle().frame(width: 26) }
            }
        }
    }

    private func phone(_ palette: Palette) -> some View {
        RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(Color(uiColor: UIColor(rgb: palette.ground)))
            .overlay(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Capsule().fill(Color(uiColor: UIColor(rgb: palette.text))).frame(width: 22, height: 4)
                    Capsule().fill(Color(uiColor: UIColor(rgb: palette.nested))).frame(width: 30, height: 6)
                    Capsule().fill(Color(uiColor: UIColor(rgb: palette.nested))).frame(width: 18, height: 6)
                    Spacer(minLength: 0)
                    HStack {
                        Spacer()
                        Capsule().fill(Color(uiColor: UIColor(rgb: palette.card))).frame(width: 24, height: 10)
                    }
                    Capsule().fill(Color(uiColor: UIColor(rgb: palette.secondary))).frame(width: 14, height: 3).frame(maxWidth: .infinity)
                }
                .padding(7)
            }
            .overlay { RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color(uiColor: UIColor(rgb: palette.nested)), lineWidth: 2) }
            .accessibilityHidden(true)
    }
}

// MARK: Chat

/// Settings › Chat: the touch feedback, and what the reactions under Dot's answers do (`/api/reactions/settings`).
struct ChatSettingsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @AppStorage(PrefKey.haptics) private var haptics: Bool = true
    @AppStorage(PrefKey.showSteps) private var showSteps: Bool = true
    @State private var reactions: Loadable<ReactionsConfig> = .loading
    @State private var problem: SettingsFailure?

    var body: some View {
        SettingsScroll(title: String(localized: "Chat", comment: "Settings row: chat preferences")) {
            GroupedSection(header: String(localized: "Feel", comment: "Chat settings section: haptics"), footer: nil) {
                ToggleRow(
                    systemImage: "hand.tap", title: String(localized: "Haptics", comment: "Chat settings: touch feedback"),
                    description: String(localized: "A light tap when you copy, swipe or send, and when an answer comes", comment: "Chat settings: what haptics do"), isOn: $haptics)
            }
            GroupedSection(
                header: String(localized: "Messages", comment: "Chat settings section: what a chat shows"),
                footer: String(localized: "Each chat's This chat sheet can show or hide them for that chat alone.", comment: "Chat settings: footer of Show steps (item 1915)")
            ) {
                ToggleRow(
                    systemImage: "list.bullet.indent", title: String(localized: "Show steps", comment: "Settings switch: a chat shows its runs' tool calls and thoughts (item 1915)"),
                    description: String(localized: "Off: only the messages, and one line that says what Dot is doing. A finished run's steps open from its Worked for line.", comment: "Description of the Show steps switch (item 1915)"),
                    isOn: $showSteps)
                .accessibilityIdentifier("settings.showSteps")
            }
            if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › General › Reactions", comment: "Where on the Mac: the reactions settings"), retry: nil) }
            LoadedPage(state: reactions, macPlace: String(localized: "Settings › General › Reactions", comment: "Where on the Mac: the reactions settings"), retry: { Task { await load() } }) { config in
                GroupedSection(
                    header: String(localized: "Reactions", comment: "Chat settings section: what reactions do"),
                    footer: String(localized: "👎 on a working task reaches it, ✋ holds it until you write, 📌 keeps a note. Which reaction does what is set on the Mac.", comment: "Chat settings: footer about reactions' meaning")
                ) {
                    ToggleRow(
                        systemImage: "arrowshape.turn.up.left", title: String(localized: "Reactions start a follow-up", comment: "Chat settings: reactions make Dot act"),
                        description: String(localized: "Off: a reaction is only feedback Dot learns from", comment: "Chat settings: what the switch off means"),
                        isOn: Binding(get: { reactions.value?.followUps ?? config.followUps }, set: { on in Task { await setFollowUps(on) } }))
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        reactions = await model.read { api throws(DaemonRequestError) in try await api.reactionSettings() }
    }

    private func setFollowUps(_ on: Bool) async {
        guard let before = reactions.value else { return }
        reactions = .loaded(ReactionsConfig(followUps: on, good: before.good, love: before.love, wrong: before.wrong, again: before.again, explain: before.explain, hold: before.hold))
        switch await model.write({ api throws(DaemonRequestError) in try await api.setReactionsFollowUps(on) }) {
        case .success(let saved):
            reactions = .loaded(saved)
            problem = nil
        case .failure(let box):
            reactions = .loaded(before)
            problem = box.failure
        }
    }
}
