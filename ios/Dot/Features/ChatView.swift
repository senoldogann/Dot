import PhotosUI
import SwiftUI

/// A chat: the transcript in one scroller (it keeps to the bottom only while the reader is at the bottom), and under
/// it, outside the scroller, the queue, the one line of the run's status and the composer.
struct ChatView: View {
    let store: NativeStore
    let thread: ThreadSummary
    @State private var model: ChatModel
    @State private var atBottom: Bool = true
    @State private var showsInspector: Bool = false
    @State private var showsModels: Bool = false
    @State private var detent: PresentationDetent = .medium
    @State private var toolRow: ToolRow?
    @State private var selecting: String?
    @State private var editing: ChatItem?
    @Environment(\.dismiss) private var dismiss: DismissAction

    init(store: NativeStore, thread: ThreadSummary, inspectorOpen: Bool = false) {
        self.store = store
        self.thread = thread
        _model = State(initialValue: ChatModel(thread: thread, store: store))
        _showsInspector = State(initialValue: inspectorOpen)
        _detent = State(initialValue: inspectorOpen ? .large : .medium)
    }

    var body: some View {
        VStack(spacing: 0) {
            Transcript(
                model: model, atBottom: $atBottom, onTool: { toolRow = $0 }, onSelect: { selecting = $0 },
                onEdit: { editing = $0 })
            VStack(spacing: 0) {
                    if let notice: String = model.notice {
                        Button { model.clearNotice() } label: {
                            Text(notice).font(Theme.meta).foregroundStyle(Theme.warn).padding(.vertical, 6).frame(maxWidth: .infinity)
                        }
                    }
                    QueuePanel(model: model)
                    StatusLine(model: model, onDetails: { showsInspector = true })
                    Composer(model: model)
            }
            .background(Theme.background)
            .overlay(alignment: .top) { Rectangle().fill(Theme.hairline).frame(height: 1) }
        }
            .background(Theme.background)
            .navigationTitle(model.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.background, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showsInspector = true } label: { Image(systemName: "sidebar.right") }
                        .accessibilityLabel("Run details")
                }
            }
            .sheet(isPresented: $showsInspector) {
                RunInspector(
                    model: model, onModels: { showsInspector = false; showsModels = true }, onExpand: { detent = .large })
                    .tint(Theme.accent)
                    .preferredColorScheme(.dark)
                    .presentationDetents([.medium, .large], selection: $detent)
                    .presentationBackground(Theme.background)
            }
            .sheet(isPresented: $showsModels) {
                NavigationStack { ModelsView(store: store) }
                    .tint(Theme.accent)
                    .preferredColorScheme(.dark)
                    .presentationDetents([.medium, .large])
                    .presentationBackground(Theme.background)
            }
            .sheet(item: $toolRow) { row in
                ToolDetailSheet(model: model, row: row)
                    .tint(Theme.accent)
                    .preferredColorScheme(.dark)
                    .presentationDetents([.medium, .large])
                    .presentationBackground(Theme.background)
            }
            .sheet(isPresented: Binding(get: { selecting != nil }, set: { if !$0 { selecting = nil } })) {
                SelectTextSheet(text: selecting ?? "")
                    .tint(Theme.accent)
                    .preferredColorScheme(.dark)
                    .presentationDetents([.medium, .large])
                    .presentationBackground(Theme.background)
            }
            .confirmationDialog(
                "Edit from here?", isPresented: Binding(get: { editing != nil }, set: { if !$0 { editing = nil } }),
                titleVisibility: .visible
            ) {
                Button("Edit from here", role: .destructive) {
                    if let item: ChatItem = editing { model.editFromHere(item) }
                    editing = nil
                }
                Button("Cancel", role: .cancel) { editing = nil }
            } message: {
                Text("The messages after this one are taken out of the chat. The files they changed stay as they are.")
            }
            .onChange(of: model.leaves) { if model.leaves { dismiss() } }
            .task { model.start() }
            .onDisappear { model.stop() }
    }
}

/// How far the reader is from the end of the transcript, and how tall it is.
private struct ScrollMetrics: Equatable {
    var fromBottom: CGFloat
    var contentHeight: CGFloat
    var viewport: CGFloat
}

/// The messages. Rows have stable ids, the list is lazy, and the scroll position follows new words only while the
/// reader is at the bottom.
private struct Transcript: View {
    let model: ChatModel
    @Binding var atBottom: Bool
    let onTool: (ToolRow) -> Void
    let onSelect: (String) -> Void
    let onEdit: (ChatItem) -> Void
    /// The reader has scrolled by hand: only then does reaching the top ask for older messages by itself.
    @State private var readerScrolled: Bool = false
    @State private var viewport: CGFloat = 0
    private let bottomId: String = "bottom"
    /// The reader's finger is on the list (or it is still coasting): only then is a distance from the end the reader's doing.
    @State private var userScrolling: Bool = false
    @State private var latest: ScrollMetrics = ScrollMetrics(fromBottom: 0, contentHeight: 0, viewport: 0)

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if model.state.items.isEmpty && model.pending.isEmpty {
                        Text(model.loaded ? "No messages yet." : "")
                            .font(Theme.small).foregroundStyle(Theme.faint).frame(maxWidth: .infinity).padding(.top, 40)
                    }
                    if model.hasOlder {
                        OlderRow(model: model).onAppear { if readerScrolled { model.loadOlder() } }
                    }
                    ForEach(model.state.items) { item in
                        MessageRow(
                            model: model, item: item, onTool: onTool, onSelect: onSelect, onEdit: onEdit,
                            onDecide: { card, approve, note in model.decide(card, approve: approve, note: note) })
                            .id(item.id)
                    }
                    ForEach(model.pending) { send in
                        PendingRow(send: send, onRetry: { model.retry(send) }, onDiscard: { model.discard(send) })
                            .id(send.id)
                    }
                    Color.clear.frame(height: 1).id(bottomId)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                // A short chat sits at the bottom, by the composer, as a long one ends there.
                .frame(minHeight: viewport, alignment: .bottom)
            }
            .scrollDismissesKeyboard(.interactively)
            .onScrollGeometryChange(for: ScrollMetrics.self) { geometry in
                // The distance left to scroll down: what the content has beyond the visible container, less what is
                // already scrolled (the offset is measured from the top inset).
                ScrollMetrics(
                    fromBottom: (geometry.contentSize.height - geometry.containerSize.height
                        - (geometry.contentOffset.y + geometry.contentInsets.top)).rounded(),
                    contentHeight: geometry.contentSize.height.rounded(),
                    viewport: geometry.containerSize.height.rounded())
            } action: { _, now in
                if abs(now.viewport - viewport) > 1 { viewport = now.viewport }
                latest = now
                // Content that grew under the reader (words streaming in) is not the reader leaving the bottom: only
                // the reader's own scrolling says where they are.
                if userScrolling { atBottom = now.fromBottom <= 32 }
            }
            .onScrollPhaseChange { old, new in
                if new == .interacting { readerScrolled = true }
                let touching: Bool = new == .interacting || new == .decelerating || new == .tracking
                if old != .idle, new == .idle, userScrolling { atBottom = latest.fromBottom <= 32 }
                userScrolling = touching
            }
            .onChange(of: scrollToken) {
                if atBottom, model.olderAnchor == nil { settle(proxy) }
            }
            // Older rows went in above the first one: the reader is put back where they were, not left at the top.
            .onChange(of: model.state.items.first?.id) {
                guard let anchor: String = model.olderAnchor else { return }
                proxy.scrollTo(anchor, anchor: .top)
                for delay in [0.05, 0.2] {
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) { proxy.scrollTo(anchor, anchor: .top) }
                }
            }
            .onAppear { settle(proxy) }
            .overlay(alignment: .bottomTrailing) {
                if !atBottom {
                    Button {
                        atBottom = true
                        withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(bottomId, anchor: .bottom) }
                    } label: {
                        Image(systemName: "arrow.down")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Theme.text)
                            .frame(width: 32, height: 32)
                            .background(Theme.raised, in: Circle())
                            .overlay(Circle().strokeBorder(Theme.hairline, lineWidth: 1))
                    }
                    .padding(12)
                    .accessibilityLabel("Jump to the newest message")
                }
            }
        }
    }

    /// Scrolls to the end, and again a moment later and once more: a lazy list guesses the height of rows it has not
    /// drawn, so the first scroll lands near the end and the passes that follow, with the rows now drawn, reach it.
    private func settle(_ proxy: ScrollViewProxy) {
        proxy.scrollTo(bottomId, anchor: .bottom)
        for delay in [0.05, 0.2] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                if atBottom { proxy.scrollTo(bottomId, anchor: .bottom) }
            }
        }
    }

    /// Changes when a row comes or the last one grows: what moves the bottom.
    private var scrollToken: Int {
        var hasher: Hasher = Hasher()
        hasher.combine(model.state.items.count)
        hasher.combine(model.pending.count)
        if let last: ChatItem = model.state.items.last {
            switch last.kind {
            case .assistant(let text, _), .answer(let text, _): hasher.combine(text.utf8.count)
            case .tool(let row): hasher.combine(row.state == .running)
            case .approval(let card): hasher.combine(card.status)
            default: break
            }
        }
        return hasher.finalize()
    }
}

private struct PendingRow: View {
    let send: PendingSend
    let onRetry: () -> Void
    let onDiscard: () -> Void

    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 0) {
                Spacer(minLength: 56)
                Text(send.shown)
                    .font(Theme.body).foregroundStyle(Theme.text.opacity(0.6))
                    .padding(.horizontal, 12).padding(.vertical, 9)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: Theme.radius))
            }
            if send.failed {
                HStack(spacing: 12) {
                    Text("Not sent").font(Theme.meta).foregroundStyle(Theme.bad)
                    Button("Retry", action: onRetry).font(Theme.meta)
                    Button("Edit", action: onDiscard).font(Theme.meta)
                }
            }
        }
    }
}

/// The one line over the composer: how long the run has worked, that it waits for the owner, or that it waits for its
/// subagents (with what the first does now). It stands outside the transcript, so it never scrolls with it.
struct StatusLine: View {
    let model: ChatModel
    let onDetails: () -> Void

    var body: some View {
        switch model.state.line {
        case .idle:
            if case .reconnecting(_, let unauthorized) = model.connection {
                line(
                    icon: unauthorized ? "key" : "arrow.triangle.2.circlepath",
                    text: unauthorized ? "This phone's key was refused. Pair again in Settings." : "Reconnecting…",
                    tone: unauthorized ? Theme.warn : Theme.faint)
            }
        case .working(let since):
            HStack(spacing: 8) {
                ProgressView().controlSize(.mini).tint(Theme.accent)
                if let since {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text("Working · \(elapsed(since, now: context.date))").font(Theme.meta).foregroundStyle(Theme.muted)
                    }
                } else {
                    Text("Working").font(Theme.meta).foregroundStyle(Theme.muted)
                }
                Spacer()
                reconnecting
            }
            .padding(.horizontal, 16).frame(height: 28)
        case .waitingOnYou(let count):
            line(icon: "bell", text: count == 1 ? "Waiting on you" : "Waiting on you · \(count)", tone: Theme.warn)
        case .helpersInBackground(let count):
            HStack(spacing: 8) {
                Image(systemName: "circle.dashed").font(.system(size: 11)).foregroundStyle(Theme.faint)
                Text(count == 1 ? "1 subagent keeps working in the background" : "\(count) subagents keep working in the background")
                    .font(Theme.meta).foregroundStyle(Theme.muted)
                Spacer()
                Button("Details", action: onDetails).font(Theme.meta).foregroundStyle(Theme.muted)
            }
            .padding(.horizontal, 16).frame(height: 28)
        case .waitingOnSubagents(let count, let now):
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    Image(systemName: "circle.dashed").font(.system(size: 11)).foregroundStyle(Theme.accent)
                    Text(count == 1 ? "Waiting on 1 subagent" : "Waiting on \(count) subagents")
                        .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.text)
                    Spacer()
                    Button("Details", action: onDetails).font(Theme.meta).foregroundStyle(Theme.muted)
                    Button("Stop") { model.stopRun() }.font(Theme.meta).foregroundStyle(Theme.muted)
                }
                if let now {
                    Text(now).font(Theme.meta).foregroundStyle(Theme.faint).lineLimit(1).padding(.leading, 19)
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 6)
        }
    }

    @ViewBuilder private var reconnecting: some View {
        if case .reconnecting = model.connection {
            Text("Reconnecting…").font(Theme.meta).foregroundStyle(Theme.faint)
        }
    }

    private func line(icon: String, text: String, tone: Color) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon).font(.system(size: 11)).foregroundStyle(tone)
            Text(text).font(Theme.meta).foregroundStyle(tone)
            Spacer()
        }
        .padding(.horizontal, 16).frame(height: 28)
    }

    private func elapsed(_ since: Double, now: Date) -> String {
        let seconds: Int = max(0, Int(now.timeIntervalSince1970 - since))
        return seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
    }
}

/// The queue: each message waiting to run, with an up and a down arrow to move it and a button to remove it. There is
/// no drag.
struct QueuePanel: View {
    let model: ChatModel

    var body: some View {
        let messages: [QueuedMessage] = model.queue.messages
        if !messages.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                Text(model.queue.held ? "Queued · paused" : "Queued · \(messages.count)")
                    .font(Theme.tiny).foregroundStyle(Theme.faint)
                    .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 2)
                ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                    HStack(spacing: 4) {
                        Text(message.text).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                        arrow("chevron.up", label: "Move up", enabled: index > 0) { model.moveQueued(message.id, by: -1) }
                        arrow("chevron.down", label: "Move down", enabled: index < messages.count - 1) { model.moveQueued(message.id, by: 1) }
                        arrow("xmark", label: "Remove", enabled: true) { model.removeQueued(message.id) }
                    }
                    .padding(.leading, 16).padding(.trailing, 6)
                }
            }
            .padding(.bottom, 4)
        }
    }

    private func arrow(_ symbol: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold))
                .foregroundStyle(enabled ? Theme.muted : Theme.faint.opacity(0.4))
                .frame(width: 36, height: 34)
                .contentShape(Rectangle())
        }
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

/// The composer: + (a photo), the text, the microphone (dictation on the phone), and Send. While a run works, Send queues the message,
/// and with the box empty the button is Stop.
struct Composer: View {
    @Bindable var model: ChatModel
    @State private var picks: [PhotosPickerItem] = []
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 6) {
            DictationLine(dictation: model.dictation)
            if !model.images.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(model.images) { image in
                            Image(uiImage: image.preview).resizable().scaledToFill().frame(width: 52, height: 52)
                                .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                        }
                    }.padding(.horizontal, 16)
                }
            }
            HStack(alignment: .bottom, spacing: 8) {
                PhotosPicker(selection: $picks, maxSelectionCount: 4, matching: .images) {
                    Image(systemName: "plus").font(.system(size: 16, weight: .medium)).foregroundStyle(Theme.muted)
                        .frame(width: 36, height: 36)
                }
                .accessibilityLabel("Attach a photo")
                TextField(model.isWorking ? "Queue a message" : "Message Dot", text: $model.draft, axis: .vertical)
                    .font(Theme.body)
                    .lineLimit(1...6)
                    .focused($focused)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.radius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
                    .accessibilityIdentifier("composer-field")
                MicButton(dictation: model.dictation) { model.toggleDictation() }
                sendButton
            }
            .padding(.horizontal, 10)
        }
        .padding(.vertical, 8)
        .onChange(of: model.draft) { model.draftChanged() }
        .onChange(of: picks) {
            let chosen: [PhotosPickerItem] = picks
            picks = []
            Task { await model.pick(chosen) }
        }
    }

    @ViewBuilder private var sendButton: some View {
        if model.isWorking && !model.canSend {
            Button { model.stopRun() } label: {
                Image(systemName: "stop.fill").font(.system(size: 13)).foregroundStyle(Theme.text)
                    .frame(width: 36, height: 36)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: Theme.radius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
            }
            .accessibilityLabel("Stop")
        } else {
            Button { model.send() } label: {
                Image(systemName: model.isWorking ? "text.append" : "arrow.up").font(.system(size: 14, weight: .bold))
                    .foregroundStyle(model.canSend ? Color.black.opacity(0.9) : Theme.faint)
                    .frame(width: 36, height: 36)
                    .background(model.canSend ? Theme.accent : Theme.raised, in: RoundedRectangle(cornerRadius: Theme.radius))
            }
            .disabled(!model.canSend)
            .accessibilityIdentifier("send")
            .accessibilityLabel(model.isWorking ? "Queue" : "Send")
        }
    }
}

/// The top of the transcript while older messages may be waiting: a line that reads them (by itself when the reader gets
/// here, or on a tap), and says when it could not.
private struct OlderRow: View {
    let model: ChatModel

    var body: some View {
        HStack {
            Spacer()
            if model.loadingOlder {
                ProgressView().controlSize(.small).tint(Theme.muted)
            } else {
                Button { model.loadOlder() } label: {
                    Text(model.olderFailed ? "Could not read older messages. Tap to try again." : "Load older messages")
                        .font(Theme.meta).foregroundStyle(model.olderFailed ? Theme.warn : Theme.muted)
                }
                .accessibilityIdentifier("load-older")
            }
            Spacer()
        }
        .frame(height: 30)
    }
}

/// A row of the chat with what a long press offers: copy, select text, react to what Dot said, edit the owner's message
/// from here; the reactions standing on it are small signs under it.
private struct MessageRow: View {
    let model: ChatModel
    let item: ChatItem
    let onTool: (ToolRow) -> Void
    let onSelect: (String) -> Void
    let onEdit: (ChatItem) -> Void
    let onDecide: (ApprovalCard, Bool, String) -> Void

    var body: some View {
        let standing: [String] = MessageActions.standing(on: item, among: model.reactions)
        VStack(alignment: .leading, spacing: 4) {
            ChatRow(item: item, onDecide: onDecide, onTool: onTool)
            if !standing.isEmpty {
                HStack(spacing: 4) {
                    ForEach(standing, id: \.self) { name in
                        Text(Reaction.symbol(of: name)).font(.system(size: 12))
                            .padding(.horizontal, 6).frame(height: 22)
                            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
                    }
                }
                .accessibilityIdentifier("reactions")
            }
        }
        .modifier(MessageMenu(model: model, item: item, standing: standing, onSelect: onSelect, onEdit: onEdit))
    }
}

private struct MessageMenu: ViewModifier {
    let model: ChatModel
    let item: ChatItem
    let standing: [String]
    let onSelect: (String) -> Void
    let onEdit: (ChatItem) -> Void

    func body(content: Content) -> some View {
        if let words: String = MessageActions.copyText(of: item) {
            content.contextMenu {
                Button { UIPasteboard.general.string = words } label: { Label("Copy", systemImage: "doc.on.doc") }
                Button { onSelect(words) } label: { Label("Select text", systemImage: "text.cursor") }
                if MessageActions.reactionTarget(of: item) != nil {
                    Menu {
                        ForEach(Reaction.all) { reaction in
                            Button { model.toggleReaction(reaction.id, on: item) } label: {
                                if standing.contains(reaction.id) {
                                    Label("\(reaction.symbol) \(reaction.label)", systemImage: "checkmark")
                                } else {
                                    Text("\(reaction.symbol) \(reaction.label)")
                                }
                            }
                        }
                    } label: { Label("React", systemImage: "face.smiling") }
                }
                if MessageActions.rewindTarget(of: item, chatIsWorking: model.isWorking) != nil {
                    Button { onEdit(item) } label: { Label("Edit from here", systemImage: "pencil") }
                }
            }
        } else {
            content
        }
    }
}

/// A message's words to select a part of and copy.
private struct SelectTextSheet: View {
    let text: String
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(text).font(Theme.body).foregroundStyle(Theme.text).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(16)
            }
            .background(Theme.background)
            .navigationTitle("Select text")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}

/// A tool step: what it was asked and what it answered, read when the sheet opens and clipped (what is left out is said).
private struct ToolDetailSheet: View {
    let model: ChatModel
    let row: ToolRow
    @State private var detail: ToolDetail?
    @State private var failed: Bool = false
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 8) {
                        Image(systemName: icon).font(.system(size: 11, weight: .bold)).foregroundStyle(tone)
                        Text(row.tool.replacingOccurrences(of: "_", with: " ").capitalizedFirst)
                            .font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.text)
                        Spacer()
                        if let seconds: Double = detail?.seconds {
                            Text(seconds < 10 ? String(format: "%.1f s", seconds) : "\(Int(seconds)) s").font(Theme.meta).foregroundStyle(Theme.faint)
                        }
                    }
                    if let target: String = detail?.target {
                        Text(target).font(Theme.meta).foregroundStyle(Theme.faint).lineLimit(2)
                    }
                    if let detail {
                        block("Arguments", clip: detail.arguments, empty: "No arguments.")
                        if let result: Clipped = detail.result {
                            block("Result", clip: result, empty: "The step returned nothing.")
                        } else {
                            Text(row.state == .running ? "Still running." : "The result of this step is not kept.")
                                .font(Theme.small).foregroundStyle(Theme.faint)
                        }
                    } else if failed {
                        Text("Dot could not be reached.").font(Theme.small).foregroundStyle(Theme.warn)
                        Button("Try again") { Task { await load() } }.buttonStyle(QuietButtonStyle())
                    } else {
                        ProgressView().controlSize(.small).tint(Theme.muted)
                    }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Theme.background)
            .navigationTitle("Step")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { await load() }
        }
    }

    private func load() async {
        failed = false
        do {
            detail = try await model.detail(of: row)
        } catch {
            failed = true
        }
    }

    private func block(_ title: String, clip: Clipped, empty: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.tiny).foregroundStyle(Theme.faint)
            Text(clip.text.isEmpty ? empty : clip.text)
                .font(Theme.mono).foregroundStyle(clip.text.isEmpty ? Theme.faint : Theme.text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .card(fill: Theme.surface)
            if clip.isCut {
                Text("\(clip.omitted) more characters are not shown. The web UI shows the whole step.")
                    .font(Theme.meta).foregroundStyle(Theme.faint)
            }
        }
    }

    private var icon: String {
        switch row.state {
        case .running: "ellipsis"
        case .ok: "checkmark"
        case .failed: "xmark"
        }
    }

    private var tone: Color { row.state == .failed ? Theme.bad : Theme.muted }
}
