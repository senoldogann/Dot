import DotEngine
#if DEBUG
import SwiftUI

/// The gallery's pages; `-DotNextGallery <page>` opens one at launch (debug builds) for screenshots without taps.
enum GalleryPage: String, CaseIterable, Identifiable, Sendable {
    case tokens
    case rows
    case controls
    case feedback
    case transcript
    case helpers
    case chrome
    case symbols

    var id: String { rawValue }

    var title: String {
        switch self {
        case .tokens: String(localized: "Colour and type", comment: "Component gallery (debug builds): a heading or sample text")
        case .rows: String(localized: "Rows and sections", comment: "Component gallery (debug builds): a heading or sample text")
        case .controls: String(localized: "Buttons, chips, status", comment: "Component gallery (debug builds): a heading or sample text")
        case .feedback: String(localized: "Empty, loading, notices, toast", comment: "Component gallery (debug builds): a heading or sample text")
        case .transcript: String(localized: "Transcript", comment: "Component gallery (debug builds): a heading or sample text")
        case .helpers: String(localized: "Helpers and background work", comment: "Component gallery (debug builds): a heading or sample text")
        case .chrome: String(localized: "Composer and floating control", comment: "Component gallery (debug builds): a heading or sample text")
        case .symbols: String(localized: "Symbols", comment: "Component gallery (debug builds): a heading or sample text")
        }
    }
}

struct GalleryIndex: View {
    var body: some View {
        List(GalleryPage.allCases) { page in
            NavigationLink(page.title) { GalleryPageView(page: page) }
                .listRowBackground(Tone.card)
        }
        .pageGround()
        .navigationTitle(String(localized: "Gallery", comment: "Component gallery (debug builds): a heading or sample text"))
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// One page of the gallery: every component of the page in its states, on the page ground.
struct GalleryPageView: View {
    let page: GalleryPage

    var body: some View {
        Group {
            switch page {
            case .symbols: SymbolSampleSheet()
            case .chrome: ChromeGallery()
            default:
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.groupedGutter) {
                        switch page {
                        case .tokens: TokensGallery()
                        case .rows: RowsGallery()
                        case .controls: ControlsGallery()
                        case .feedback: FeedbackGallery()
                        case .transcript: TranscriptGallery()
                        case .helpers: HelpersGallery()
                        case .chrome, .symbols: EmptyView()
                        }
                    }
                    .padding(.horizontal, Space.gutter)
                    .padding(.vertical, Space.groupedGutter)
                    .readableColumn()
                }
            }
        }
        .background(Tone.ground)
        .navigationTitle(page.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct Swatch: View {
    let name: String
    let fill: Color

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            // A hairline on the swatch only, so a surface shows on the card it would otherwise match (gallery only).
            rounded(Radius.inner).fill(fill).frame(height: 44)
                .overlay(rounded(Radius.inner).strokeBorder(Tone.separator, lineWidth: 1))
            Text(name).textRole(.caption).foregroundStyle(Tone.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(name)
    }
}

private struct TokensGallery: View {
    var body: some View {
        GroupedSection(header: String(localized: "Surfaces (no strokes)", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "Ground · card and grouped · nested and pressed · sheet · opaque glass for Reduce Transparency.", comment: "Component gallery (debug builds): a heading or sample text")) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 92), spacing: Space.s)], spacing: Space.s) {
                Swatch(name: String(localized: "ground", comment: "Component gallery (debug builds): the name of a surface colour token"), fill: Tone.ground)
                Swatch(name: String(localized: "card", comment: "Component gallery (debug builds): the name of a surface colour token"), fill: Tone.card)
                Swatch(name: String(localized: "nested", comment: "Component gallery (debug builds): the name of a surface colour token"), fill: Tone.nested)
                Swatch(name: String(localized: "sheet", comment: "Component gallery (debug builds): the name of a surface colour token"), fill: Tone.sheet)
                Swatch(name: String(localized: "glass fallback", comment: "Component gallery (debug builds): the name of a surface colour token"), fill: Tone.glassFallback)
            }
            .padding(Space.groupedGutter)
        }
        GroupedSection(header: String(localized: "Ink", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "Ink is the only accent. Amber means it waits on you; red means it failed.", comment: "Component gallery (debug builds): a heading or sample text")) {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text(String(localized: "Primary words", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.body).foregroundStyle(Tone.text)
                Text(String(localized: "Secondary words", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.body).foregroundStyle(Tone.secondary)
                Text(String(localized: "Tertiary words", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.body).foregroundStyle(Tone.tertiary)
                Text(String(localized: "Waiting on you", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.body).foregroundStyle(Tone.warn)
                Text(String(localized: "Failed", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.body).foregroundStyle(Tone.failure)
                HStack(spacing: Space.xs) {
                    Image(systemName: "chevron.right").foregroundStyle(Tone.glyph)
                    Text(String(localized: "glyph (3:1)", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.caption).foregroundStyle(Tone.tertiary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.groupedGutter)
        }
        GroupedSection(header: String(localized: "Type roles", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "System font, scaled with Dynamic Type; body 16/23.", comment: "Component gallery (debug builds): a heading or sample text")) {
            VStack(alignment: .leading, spacing: Space.xs) {
                ForEach(TypeRole.allCases, id: \.self) { role in
                    Text(verbatim: "\(role.rawValue) \(Int(role.points.size))/\(Int(role.points.lineHeight)) · 12:04")
                        .textRole(role)
                        .tabularNumbers()
                        .foregroundStyle(Tone.text)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.groupedGutter)
        }
    }
}

private struct RowsGallery: View {
    @State private var notify: Bool = true
    @State private var haptics: Bool = false
    @State private var choice: String = String(localized: "System", comment: "Component gallery (debug builds): a heading or sample text")

    var body: some View {
        GroupedSection(header: String(localized: "List rows", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "49 pt, 63 with a description; no separators inside a section.", comment: "Component gallery (debug builds): a heading or sample text")) {
            ListRow(systemImage: "desktopcomputer", title: String(localized: "This Mac", comment: "Component gallery (debug builds): a heading or sample text"), description: nil, accessory: .detailAndChevron("mac.tailnet.ts.net"))
            ListRow(systemImage: "bell", title: String(localized: "Notifications", comment: "Component gallery (debug builds): a heading or sample text"), description: String(localized: "Needs you, questions, done", comment: "Component gallery (debug builds): a heading or sample text"), accessory: .chevron)
            ListRow(systemImage: "info.circle", title: String(localized: "Version", comment: "Component gallery (debug builds): a heading or sample text"), description: nil, accessory: .detail("0.1.0 (1)"))
            ListRow(systemImage: nil, title: String(localized: "A row with no glyph", comment: "Component gallery (debug builds): a heading or sample text"), description: nil, accessory: .none)
        }
        GroupedSection(header: String(localized: "Toggles", comment: "Component gallery (debug builds): a heading or sample text"), footer: nil) {
            ToggleRow(systemImage: "bell.badge", title: String(localized: "Needs you", comment: "Component gallery (debug builds): a heading or sample text"), description: String(localized: "An approval or a question waits.", comment: "Component gallery (debug builds): a heading or sample text"), isOn: $notify)
            ToggleRow(systemImage: "hand.tap", title: String(localized: "Haptics while text streams", comment: "Component gallery (debug builds): a heading or sample text"), description: nil, isOn: $haptics)
        }
        GroupedSection(header: String(localized: "Appearance", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "Only the checkmark marks the chosen row.", comment: "Component gallery (debug builds): a heading or sample text")) {
            ForEach([String(localized: "System", comment: "Component gallery (debug builds): a heading or sample text"), String(localized: "Light", comment: "Component gallery (debug builds): a heading or sample text"), String(localized: "Dark", comment: "Component gallery (debug builds): a heading or sample text")], id: \.self) { option in
                RadioRow(title: option, description: nil, isSelected: choice == option) { choice = option }
            }
        }
    }
}

private struct ControlsGallery: View {
    @State private var filter: String = String(localized: "All", comment: "Component gallery (debug builds): a heading or sample text")

    var body: some View {
        GroupedSection(header: String(localized: "Buttons", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "Ink primary · neutral secondary · red words on a neutral fill.", comment: "Component gallery (debug builds): a heading or sample text")) {
            VStack(alignment: .leading, spacing: Space.s) {
                Button(String(localized: "Allow once", comment: "Component gallery (debug builds): a heading or sample text"), action: {}).buttonStyle(PrimaryButtonStyle())
                Button(String(localized: "Deny", comment: "Component gallery (debug builds): a heading or sample text"), action: {}).buttonStyle(SecondaryButtonStyle())
                Button(String(localized: "Delete chat", comment: "Component gallery (debug builds): a heading or sample text"), role: .destructive, action: {}).buttonStyle(DestructiveButtonStyle())
                Button(String(localized: "Disabled", comment: "Component gallery (debug builds): a heading or sample text"), action: {}).buttonStyle(PrimaryButtonStyle()).disabled(true)
                Button(action: {}) {
                    Text(String(localized: "Allow once", comment: "Component gallery (debug builds): a heading or sample text"))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
            }
            .padding(Space.groupedGutter)
        }
        GroupedSection(header: String(localized: "Chips", comment: "Component gallery (debug builds): a heading or sample text"), footer: nil) {
            ScrollView(.horizontal) {
                HStack(spacing: Space.xs) {
                    ForEach([String(localized: "All", comment: "Component gallery (debug builds): a heading or sample text"), String(localized: "Needs you", comment: "Component gallery (debug builds): a heading or sample text"), String(localized: "Working", comment: "Component gallery (debug builds): a heading or sample text"), String(localized: "Settled", comment: "Component gallery (debug builds): a heading or sample text")], id: \.self) { name in
                        Chip(title: name, systemImage: nil, isSelected: filter == name) { filter = name }
                    }
                    Chip(title: String(localized: "Default · medium", comment: "Component gallery (debug builds): a heading or sample text"), systemImage: "cpu", isSelected: false) {}
                }
                .padding(.horizontal, Space.groupedGutter)
            }
            .scrollIndicators(.hidden)
        }
        GroupedSection(header: String(localized: "Status words", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "The order is the priority when several hold.", comment: "Component gallery (debug builds): a heading or sample text")) {
            VStack(alignment: .leading, spacing: Space.s) {
                ForEach(ChatStatus.allCases, id: \.self) { StatusWord(status: $0) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.groupedGutter)
        }
        GroupedSection(header: String(localized: "Agents", comment: "Component gallery (debug builds): a heading or sample text"), footer: String(localized: "Dot wears its face; another agent a neutral disc.", comment: "Component gallery (debug builds): a heading or sample text")) {
            HStack(spacing: Space.m) {
                DotFace(size: 36)
                AgentAvatar(size: 36)
                SpeakerLine(agent: "Dot")
                SpeakerLine(agent: "Research")
            }
            .padding(Space.groupedGutter)
        }
    }
}

private struct FeedbackGallery: View {
    @State private var toast: ToastMessage? = ToastMessage(
        id: UUID(), text: String(localized: "Removed from the queue", comment: "Component gallery (debug builds): a heading or sample text"), systemImage: "checkmark.circle", actionTitle: String(localized: "Undo", comment: "Component gallery (debug builds): a heading or sample text"))

    var body: some View {
        Notice(tone: .info, message: String(localized: "Offline · saved 4 min ago", comment: "Component gallery (debug builds): a heading or sample text"), dismiss: nil)
        Notice(tone: .warn, message: String(localized: "Dot waits for you in 2 chats", comment: "Component gallery (debug builds): a heading or sample text"), dismiss: {})
        Notice(tone: .failure, message: String(localized: "The Mac refused this phone's key. Pair again.", comment: "Component gallery (debug builds): a heading or sample text"), dismiss: {})
        GroupedSection(header: String(localized: "Empty", comment: "Component gallery (debug builds): a heading or sample text"), footer: nil) {
            EmptyState(
                title: String(localized: "No chats yet", comment: "Component gallery (debug builds): a heading or sample text"), systemImage: "bubble.left.and.bubble.right",
                detail: String(localized: "Ask Dot for anything: a task, a lesson, an errand on the Mac.", comment: "Dot's empty chat (and the component gallery): what to ask Dot"), actionTitle: String(localized: "New chat", comment: "Component gallery (debug builds): a heading or sample text"),
                action: {})
        }
        GroupedSection(header: String(localized: "Loading", comment: "Component gallery (debug builds): a heading or sample text"), footer: nil) {
            LoadingState(message: String(localized: "Reading your chats…", comment: "Component gallery (debug builds): a heading or sample text")).frame(height: 160)
        }
        Color.clear.frame(height: 72)
            .toastHost($toast, seconds: 600) { _ in }
    }
}

private struct TranscriptGallery: View {
    var body: some View {
        UserBubble(text: String(localized: "Why does the parser test fail only on CI?", comment: "Component gallery (debug builds): a heading or sample text"))
        UserBubble(text: String(localized: "Short one", comment: "Component gallery (debug builds): a heading or sample text"))
        SpeakerLine(agent: "Dot")
        ToolGroup(summary: String(localized: "Worked for 2m 14s · 5 steps · 1 failed", comment: "Component gallery (debug builds): a heading or sample text"), calls: Samples.toolCalls, startsOpen: true)
        ToolGroup(summary: String(localized: "Worked for 48s · 4 steps", comment: "Component gallery (debug builds): a heading or sample text"), calls: Samples.computerUse, startsOpen: false)
        HelperGroup(helpers: Samples.helpers, open: { _ in })
        AnswerText(text: Samples.answer())
        CodeBlock(language: "python", code: Samples.code)
    }
}

private struct ChromeGallery: View {
    @State private var empty: String = ""
    @State private var long: String =
        String(localized: "Book the dentist for next week, afternoons if you can, and tell me the price before you pay anything.", comment: "Component gallery (debug builds): a heading or sample text")

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                Text(String(localized: "Composer at rest", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.footnote).foregroundStyle(Tone.secondary)
                ComposerView(text: $empty, attachments: .constant([]), quote: .constant(nil), placeholder: String(localized: "Message Dot", comment: "Component gallery (debug builds): a heading or sample text"), buttons: composerButtons(hasContent: !empty.isEmpty, runActive: false, followUp: .queue, editing: false), perform: { _ in }, model: ModelChoice(ref: "default", label: "Opus 5.5", effort: "medium"), openSettings: {}, suggestions: .none)
                Text(String(localized: "Composer expanded", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.footnote).foregroundStyle(Tone.secondary)
                ComposerView(text: $long, attachments: .constant([]), quote: .constant(nil), placeholder: String(localized: "Message Dot", comment: "Component gallery (debug builds): a heading or sample text"), buttons: composerButtons(hasContent: !long.isEmpty, runActive: false, followUp: .queue, editing: false), perform: { _ in }, model: ModelChoice(ref: "default", label: "Opus 5.5", effort: "medium"), openSettings: {}, suggestions: .none)
                Text(String(localized: "Floating control", comment: "Component gallery (debug builds): a heading or sample text")).textRole(.footnote).foregroundStyle(Tone.secondary)
                FloatingWorkingControl(
                    summary: WorkingSummary(status: .working, activity: nil, since: Date(timeIntervalSinceNow: -192), queued: 2, helpersDone: 1, helpersTotal: 3, plan: PlanProgress(done: 1, total: 5), canStop: true),
                    showsScrollToBottom: true, openStatus: {}, stop: {}, openPlan: {}, openQueue: {}, openHelpers: {}, scrollToBottom: {})
                FloatingWorkingControl(
                    summary: WorkingSummary(status: .waitingOnYou, activity: nil, since: nil, queued: 0, helpersDone: 0, helpersTotal: 0, plan: nil, canStop: false),
                    showsScrollToBottom: false, openStatus: {}, stop: {}, openPlan: {}, openQueue: {}, openHelpers: {}, scrollToBottom: {})
                FloatingWorkingControl(
                    summary: WorkingSummary(status: nil, activity: nil, since: nil, queued: 0, helpersDone: 0, helpersTotal: 0, plan: nil, canStop: false),
                    showsScrollToBottom: true, openStatus: {}, stop: {}, openPlan: {}, openQueue: {}, openHelpers: {}, scrollToBottom: {})
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
        }
    }
}

private struct HelpersGallery: View {
    var body: some View {
        GroupedSection(header: String(localized: "Agents", comment: "Title of the sheet of a chat's helpers"), footer: nil) {
            VStack(spacing: 0) {
                ForEach(Samples.helperRuns(now: .now)) { run in
                    HelperRow(run: run, open: {}, stop: {})
                }
            }
            .padding(.horizontal, Space.groupedGutter)
        }
        GroupedSection(header: String(localized: "Background work", comment: "Title of the sheet of what still works for a chat"), footer: nil) {
            VStack(spacing: 0) {
                ForEach(Samples.backgroundWork) { work in BackgroundWorkRow(work: work, open: {}) }
            }
            .padding(.horizontal, Space.groupedGutter)
        }
    }
}

/// The sample chat of wave N0, kept as the gallery's fixture (the shipped chat is `ChatScreen`, on the store).
struct SampleChatScreen: View {
    let chat: SampleChat
    @State private var draft: String = ""
    @State private var panel: ChatPanel?

    init(chat: SampleChat, initialPanel: ChatPanel?) {
        self.chat = chat
        _panel = State(initialValue: initialPanel)
    }

    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.m) {
                UserBubble(text: String(localized: "Book me the dentist for next week, afternoons if you can.", comment: "Sample owner message in the placeholder screen of wave N0"))
                SpeakerLine(agent: "Dot")
                ToolGroup(summary: String(localized: "Worked for 2m 14s · 4 steps", comment: "Work-log group summary (sample)"), calls: Samples.computerUse, startsOpen: true)
                HelperGroup(helpers: Samples.helpers, open: { _ in })
                AnswerText(text: Samples.booking())
                Notice(tone: .warn, message: String(localized: "Dot waits for you: pay the 20 € deposit?", comment: "Sample notice: Dot waits for a payment approval"), dismiss: nil)
            }
            .frame(maxWidth: proseWidth)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.m)
        }
        .defaultScrollAnchor(.bottom)
        .scrollDismissesKeyboard(.interactively)
        .background(Tone.ground)
        .navigationTitle(chat.title)
        .navigationSubtitle(subtitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(sizeClass == .regular ? .automatic : .hidden, for: .tabBar)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button(
                    String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"),
                    systemImage: "folder"
                ) { panel = .files }
                Button(String(localized: "Agents", comment: "Chat header quick panel: the chat's helpers"), systemImage: "person.2") {
                    panel = .agents
                }
                Button(
                    String(localized: "The Mac's screen", comment: "Chat header quick panel: the Mac's live screen"),
                    systemImage: "desktopcomputer"
                ) { panel = .screen }
            }
        }
        .sheet(item: $panel) { panel in
            switch panel {
            case .agents: AgentsSheet(runs: Samples.helperRuns(now: .now), close: { self.panel = nil })
            case .background: BackgroundWorkSheet(items: Samples.backgroundWork, close: { self.panel = nil })
            case .files, .queue, .inspector, .model, .plan:
                EmptyState(
                    title: String(localized: "No changes yet", comment: "Changes panel empty state"), systemImage: "folder",
                    detail: String(localized: "Files the run changes show here.", comment: "Changes panel empty state detail"),
                    actionTitle: nil, action: {}
                )
                .inspectorSheet(
                    title: String(localized: "Changes and files", comment: "Chat header quick panel: what the run changed"),
                    close: { self.panel = nil })
            case .screen:
                EmptyState(
                    title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"),
                    systemImage: "desktopcomputer",
                    detail: String(localized: "The live screen comes in a later version.", comment: "Mac screen panel placeholder"),
                    actionTitle: nil, action: {}
                )
                .inspectorSheet(
                    title: String(localized: "The Mac's screen", comment: "Chat header quick panel: the Mac's live screen"),
                    close: { self.panel = nil })
            }
        }
        .safeAreaInset(edge: .bottom) {
            VStack(alignment: .trailing, spacing: Space.xs) {
                FloatingWorkingControl(
                    summary: WorkingSummary(status: .waitingOnYou, activity: nil, since: Date(timeIntervalSinceNow: -180), queued: 2, helpersDone: 0, helpersTotal: 2, plan: nil, canStop: true),
                    showsScrollToBottom: true, openStatus: { panel = .background }, stop: {}, openPlan: {}, openQueue: {}, openHelpers: { panel = .agents },
                    scrollToBottom: {})
                    .frame(maxWidth: .infinity, alignment: .center)
                ComposerView(text: $draft, attachments: .constant([]), quote: .constant(nil), placeholder: String(localized: "Message Dot", comment: "Placeholder of the composer"), buttons: composerButtons(hasContent: !draft.isEmpty, runActive: false, followUp: .queue, editing: false), perform: { _ in }, model: ModelChoice(ref: "default", label: "Opus 5.5", effort: "medium"), openSettings: {}, suggestions: .none)
            }
            .frame(maxWidth: proseWidth)
            .padding(.horizontal, Space.s)
            .padding(.bottom, Space.xs)
        }
    }

    /// Who and where (plan §18): the agent and the Mac.
    private var subtitle: String { [chat.agent, Samples.macName].joined(separator: " · ") }

    /// The reading column: about 68 characters of the body font on a wide screen, the full width on a phone.
    private var proseWidth: CGFloat? {
        guard sizeClass == .regular else { return nil }
        return ProseColumn.width(charactersPerLine: ProseColumn.target, font: ProseColumn.bodyFont(dynamicTypeSize))
    }
}

#endif
