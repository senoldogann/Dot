#if DEBUG
import SwiftUI

/// Debug builds only (`-DotGallery 1`): the app's pieces on one screen with made-up data, for looking at what is hard
/// to reach with a scratch daemon (a step that asks, a secret, a failed step, an empty state, the buttons). It is how
/// the design pass was checked in the simulator; it ships in no release build.
struct DesignGallery: View {
    let page: Int

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.l) {
                switch page {
                case 2: components
                case 3: states
                case 4: dock
                default: rows
                }
            }
            .padding(Space.l)
        }
        .background { DotBackground() }
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
                Avatar(id: "dot", name: "Dot", size: 44, state: .working)
                Avatar(id: "agt1", name: "Invoices", size: 44, state: .waiting)
                Avatar(id: "agt2", name: "Research", size: 44)
                DotGlyph(size: 44)
            }
            .padding(.vertical, Space.s)
            HStack(spacing: Space.s) {
                StatusPill(text: "Needs you", icon: .hand, tone: Theme.warn, pulse: true)
                StatusPill(text: "Working", icon: .audioWaveform, tone: Theme.accent, pulse: true)
                StatusPill(text: "Done", icon: .check, tone: Theme.good)
                StatusPill(text: "Failed", tone: Theme.bad)
            }
            DotGlassGroup {
                HStack(spacing: Space.m) {
                    Button("Deny") {}.dotButton(.glass)
                    Button("Allow") {}.dotButton(.prominent)
                    GlassCircleButton(icon: .arrowDown, label: "Down") {}
                }
                .controlSize(.large)
            }
            HStack(spacing: Space.s) {
                DotIcon(.audioWaveform, size: 16).foregroundStyle(Theme.accent)
                Text("Working · 2m 9s").font(Theme.meta.weight(.medium).monospacedDigit())
            }
            .padding(.horizontal, Space.m).frame(minHeight: 32)
            .dotGlass(in: Capsule())
            VStack(spacing: 0) {
                CardRow(icon: .cpu, title: "Models", detail: "The main and the review model", showsChevron: true)
                CardDivider()
                CardRow(icon: .key, tint: Theme.bad, title: "Forget this phone's key")
            }
            .card()
            NoticeCard(words: "The daemon refused that model.")
            Text("Type: title2 / headline / callout / footnote / caption")
                .font(Theme.body).foregroundStyle(Theme.text)
            Text("SF Mono: let run = try await model.send()").font(Theme.mono).foregroundStyle(Theme.muted)
            HStack(spacing: Space.s) {
                swatch(Theme.background, "bg"); swatch(Theme.surface, "surface"); swatch(Theme.raised, "raised")
                swatch(Theme.accentFill, "accent"); swatch(Theme.warn, "warn"); swatch(Theme.bad, "bad"); swatch(Theme.good, "ok")
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
