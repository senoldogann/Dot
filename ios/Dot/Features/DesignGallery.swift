#if DEBUG
import SwiftUI

/// Debug builds only (`-DotGallery 1`): the app's pieces on one screen with made-up data, for looking at what is hard
/// to reach with a scratch daemon (a step that asks, a secret, a failed step, an empty state, the buttons). It is how
/// the design pass was checked in the simulator; it ships in no release build. Page 2 is the look's one language on one screen
/// (the palette, the state marks, the three buttons, the chip, the card, the row, the section) and page 5 puts the sheet recipe
/// over it (a real sheet, with the system's "Done").
struct DesignGallery: View {
    let page: Int
    @State private var showsSheet: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.l) {
                switch page {
                case 2, 5: components
                case 3: states
                case 4: dock
                default: rows
                }
            }
            .padding(Space.l)
        }
        .background { DotBackground() }
        .sheet(isPresented: $showsSheet) { sheetRecipe.dotSheet().presentationDetents([.medium, .large]) }
        .task {
            if page == 5 {
                try? await Task.sleep(for: .milliseconds(700))
                showsSheet = true
            }
        }
    }

    /// The sheet recipe in one place: the app's ground, a title, ONE trailing "Done", content in cards, a section header in the
    /// secondary colour and a footer in the quiet tier.
    private var sheetRecipe: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    CardGroup(title: "A group", footer: "The footer is in the quiet tier, sentence case, plain.") {
                        CardRow(icon: .cpu, title: "A row", detail: "A monochrome glyph, words, a chevron", showsChevron: true)
                        CardDivider()
                        CardRow(icon: .bell, title: "A row with a value") {
                            Text("Value").font(Theme.small).foregroundStyle(Theme.muted)
                        }
                    }
                    HStack(spacing: Space.s) {
                        Button { } label: { Text("Deny").frame(maxWidth: .infinity) }.dotButton(.secondary)
                        Button { } label: { Text("Allow once").frame(maxWidth: .infinity) }.dotButton(.primary)
                    }
                    .controlSize(.large)
                }
                .padding(Space.l)
            }
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Sheet")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { DoneButton { showsSheet = false } }
        }
    }

    private func item(_ kind: ChatItem.Kind, _ id: String) -> some View {
        ChatRow(item: ChatItem(id: id, kind: kind, ts: 1), onDecide: { _, _, _ in })
    }

    private var rows: some View {
        VStack(alignment: .leading, spacing: 14) {
            item(.user("Compare the two folders and tell me what changed. https://example.com/report"), "u")
            item(
                .assistant(
                    text: "Two folders changed: **Invoices** has four new files. The details are in https://example.com/week-47.\n\n```\nls -l Invoices | wc -l\n4\n```",
                    streaming: false), "a")
            VStack(alignment: .leading, spacing: 0) {
                item(.tool(ToolRow(callId: "1", runId: "r", tool: "run_shell", line: "Shell: ls -l ~/Documents/Invoices", state: .ok)), "t1")
                item(.tool(ToolRow(callId: "2", runId: "r", tool: "read_file", line: "Read file: summary-week-47.md", state: .ok)), "t2")
                item(.tool(ToolRow(callId: "3", runId: "r", tool: "web_search", line: "Web search: invoice reminder wording", state: .running)), "t3")
                item(.tool(ToolRow(callId: "4", runId: "r", tool: "delegate", line: "Delegate: read the receipts folder", state: .failed)), "t4")
            }
            item(.approval(ApprovalCard(approvalId: "p1", runId: "r", tool: "run_shell", summary: "mv ~/Documents/Invoices/old.pdf ~/Archive/", severity: "high", question: nil, status: "pending")), "ap1")
            item(.approval(ApprovalCard(approvalId: "p2", runId: "r", tool: "ask_user", summary: "", severity: "question", question: "Which folder should I move the old invoices to?", status: "pending")), "ap2")
            item(.approval(ApprovalCard(approvalId: "p3", runId: "r", tool: "ask_secret", summary: "Demo API token", severity: "secret", question: nil, status: "pending")), "ap3")
            item(.approval(ApprovalCard(approvalId: "p4", runId: "r", tool: "write_file", summary: "Write notes.md", severity: "medium", question: nil, status: "approved")), "ap4")
            item(.approval(ApprovalCard(approvalId: "p5", runId: "r", tool: "send_email", summary: "To: anna@example.com", severity: "high", question: nil, status: "denied")), "ap5")
            item(.answer(text: "Stopped before it finished.", status: "cancelled"), "x")
            item(.note("Reconnected. 3 events caught up.", .plain), "n")
        }
    }

    private var components: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            HStack(spacing: Space.l) {
                AgentAvatar(id: "dot", size: 44, state: .working)
                AgentAvatar(id: "agt1", size: 44, state: .waiting)
                AgentAvatar(id: "agt2", size: 44, state: .working)
                AgentAvatar(id: "agt3", size: 44)
                DotGlyph(size: 44)
            }
            .padding(.vertical, Space.s)
            VStack(alignment: .leading, spacing: Space.s) {
                SectionLabel(title: "State marks")
                HStack(spacing: Space.l) {
                    StateMark(words: "Running", kind: .working)
                    StateMark(words: "Waiting on you", kind: .waiting)
                }
                HStack(spacing: Space.l) {
                    StateMark(words: "Failed", kind: .failed)
                    StateMark(words: "Done", kind: .done)
                    StateMark(words: "Cancelled", kind: .quiet)
                }
            }
            VStack(alignment: .leading, spacing: Space.s) {
                SectionLabel(title: "Buttons")
                HStack(spacing: Space.s) {
                    Button("Allow once") {}.dotButton(.primary)
                    Button("Deny") {}.dotButton(.secondary)
                    Button("Forget") {}.dotButton(.destructive)
                }
                HStack(spacing: Space.s) {
                    Button("Send") {}.dotButton(.primary).disabled(true)
                    Button("Try again") {}.dotButton(.quiet)
                    GlassCircleButton(icon: .arrowDown, label: "Down") {}
                }
            }
            VStack(alignment: .leading, spacing: Space.s) {
                SectionLabel(title: "Chip")
                DotChip(icon: .cpu, words: "Opus 5.5 · High") {}
            }
            HStack(spacing: Space.s) {
                DotIcon(.audioWaveform, size: 16).foregroundStyle(Theme.text)
                Text("Working · 2m 9s").font(Theme.meta.weight(.medium).monospacedDigit())
            }
            .padding(.horizontal, Space.m).frame(minHeight: 44)
            .dotGlass(in: Capsule())
            CardGroup(title: "A group", footer: "Footers are in the quiet tier.") {
                CardRow(icon: .cpu, title: "Models", detail: "The main and the review model", showsChevron: true)
                CardDivider()
                CardRow(icon: .key, title: "Forget this phone's key", destructive: true)
            }
            NoticeCard(words: "Your Mac refused that model. Pick another.")
            Text("Type: title2 / headline / callout / footnote / caption")
                .font(Theme.body).foregroundStyle(Theme.text)
            Text("SF Mono: let run = try await model.send()").font(Theme.mono).foregroundStyle(Theme.muted)
            HStack(spacing: Space.s) {
                swatch(Theme.background, "ground"); swatch(Theme.surface, "surface"); swatch(Theme.raised, "raised")
                swatch(Theme.action, "action"); swatch(Theme.warn, "warn"); swatch(Theme.bad, "bad")
            }
        }
    }

    /// The capsule over the composer in each state it has (item 1390), each with the round jump button beside it as the
    /// chat draws it.
    private var dock: some View {
        let now: Double = Date().timeIntervalSince1970
        func messages(_ count: Int, held: Bool = false) -> QueueView {
            QueueView(
                held: held, messages: (0..<count).map { QueuedMessage(id: "q\($0)", position: $0, text: "m\($0)", createdAt: 1) },
                besideRunId: nil, besideThreadId: nil)
        }
        func loops(_ whens: [String]) -> [ThreadAutomation] {
            whens.enumerated().compactMap { index, when in
                let json: String = """
                    {"schedule": {"record": {"id": "s\(index)", "name": "n", "cron": "*/10 * * * *", "run_at": null, "goal": "g",
                      "enabled": true, "next_run_at": null, "last_run_at": null, "thread_id": "t", "last_status": ""},
                      "when": "\(when)", "next_local": "03:50", "agent": null, "brief": false}, "loop": true}
                    """
                return try? JSONDecoder().decode(ThreadAutomation.self, from: Data(json.utf8))
            }
        }
        func row(_ caption: String, _ line: RunLine, queue: QueueView = messages(0), connection: FeedState = .live, automations: [ThreadAutomation] = []) -> some View {
            VStack(alignment: .leading, spacing: Space.s) {
                Text(caption).font(Theme.meta).foregroundStyle(Theme.faint)
                DotGlassGroup(spacing: Space.m) {
                    HStack(spacing: Space.s) {
                        DockCapsule(
                            summary: DockSummary.make(line: line, connection: connection, queue: queue, automations: automations), onOpen: { _ in })
                        GlassCircleButton(icon: .arrowDown, label: "Jump to the newest message") {}
                    }
                }
                .frame(maxWidth: .infinity)
            }
        }
        return VStack(alignment: .leading, spacing: Space.xl) {
            row("Working, a loop, three queued", .working(since: now - 129), queue: messages(3), automations: loops(["every 10 minutes"]))
            row("Waiting on you, one queued", .waitingOnYou(count: 1), queue: messages(1))
            row("Waiting on two helpers", .waitingOnSubagents(count: 2, now: "Reads the receipts folder"), automations: loops(["every 10 minutes", "every hour"]))
            row("Helpers in the background, the queue held", .helpersInBackground(count: 2), queue: messages(2, held: true))
            row("Working while the stream reconnects, twelve queued", .working(since: now - 20), queue: messages(12), connection: .reconnecting(attempt: 2, unauthorized: false))
            row("The stream cannot connect", .idle, connection: .reconnecting(attempt: 2, unauthorized: false))
            row("This phone's key was refused", .idle, connection: .reconnecting(attempt: 1, unauthorized: true))
            row("Only the queue", .idle, queue: messages(4))
        }
    }

    private func swatch(_ color: Color, _ name: String) -> some View {
        VStack(spacing: 4) {
            RoundedRectangle(cornerRadius: Radius.s).fill(color).frame(height: 40)
                .overlay(RoundedRectangle(cornerRadius: Radius.s).strokeBorder(Theme.hairline, lineWidth: 1))
            Text(name).font(Theme.tiny).foregroundStyle(Theme.muted)
        }
    }

    private var states: some View {
        VStack(spacing: Space.xxl) {
            DotEmptyState(
                icon: .message, title: "No chats yet",
                message: "Message Dot from the Mac, or start a chat here.", actionTitle: "New chat", action: {})
            DotEmptyState(
                icon: .monitorOff, title: "Dot can't reach your Mac",
                message: "Is the Mac awake, Dot running and Tailscale on here?", tone: Theme.warn, actionTitle: "Try again",
                action: {})
        }
        .padding(.top, Space.xl)
    }
}
#endif
