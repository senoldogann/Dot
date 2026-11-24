import PhotosUI
import SwiftUI

/// A chat: the transcript in one scroller (it keeps to the bottom only while the reader is at the bottom), and over its
/// foot, floating on glass, the queue, the one line of the run's status and the composer. The transcript runs under
/// them, softened at its edge, so that nothing is cut off by a bar.
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
        Transcript(
            model: model, atBottom: $atBottom, onTool: { toolRow = $0 }, onSelect: { selecting = $0 },
            onEdit: { editing = $0 }
        )
        .dotScrollEdges()
        .safeAreaInset(edge: .bottom, spacing: 0) {
            VStack(spacing: Space.s) {
                if let notice: String = model.notice {
                    Button { model.clearNotice() } label: {
                        Label(notice, systemImage: "info.circle")
                            .font(Theme.meta).foregroundStyle(Theme.warn)
                            .padding(.horizontal, Space.m).frame(minHeight: 32)
                    }
                    .buttonStyle(.plain)
                    .dotGlass(in: Capsule(), tint: Theme.warn.opacity(0.2))
                }
                QueuePanel(model: model).dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                StatusLine(model: model, onDetails: { showsInspector = true }).dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                Composer(model: model).dynamicTypeSize(...DynamicTypeSize.accessibility2)
            }
            .padding(.horizontal, Space.s + 2)
            .padding(.bottom, Space.xs)
            .padding(.top, Space.xs)
            .dotAnimation(Motion.snappy, value: model.queue.messages.count)
        }
        .dotScreenBackground()
        .navigationTitle(model.title)
        .navigationBarTitleDisplayMode(.inline)
        .dotSubtitle(subtitle)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showsInspector = true } label: { Image(systemName: "info.circle") }
                    .accessibilityLabel("Run details")
            }
        }
        .sheet(isPresented: $showsInspector) {
            RunInspector(
                model: model, onModels: { showsInspector = false; showsModels = true }, onExpand: { detent = .large }
            )
            .dotSheet()
            .presentationDetents([.medium, .large], selection: $detent)
        }
        .sheet(isPresented: $showsModels) {
            NavigationStack { ModelsView(store: store) }
                .dotSheet()
                .presentationDetents([.medium, .large])
        }
        .sheet(item: $toolRow) { row in
            ToolDetailSheet(model: model, row: row)
                .dotSheet()
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: Binding(get: { selecting != nil }, set: { if !$0 { selecting = nil } })) {
            SelectTextSheet(text: selecting ?? "")
                .dotSheet()
                .presentationDetents([.medium, .large])
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

    /// The line under the title (iOS 26): how the run stands.
    private var subtitle: String {
        switch model.state.line {
        case .idle: ""
        case .working: "Working"
        case .waitingOnYou: "Waiting on you"
        case .waitingOnSubagents(let count, _): "Waiting on \(count) subagent\(count == 1 ? "" : "s")"
        case .helpersInBackground(let count): "\(count) subagent\(count == 1 ? "" : "s") in the background"
        }
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
                LazyVStack(alignment: .leading, spacing: 0) {
                    if model.state.items.isEmpty && model.pending.isEmpty && model.loaded {
                        DotEmptyState(
                            symbol: "text.bubble", title: "No messages yet",
                            message: "Say what you want done. Dot works on it and asks you only when it must.")
                            .padding(.top, Space.xxl)
                    }
                    if model.hasOlder {
                        OlderRow(model: model).onAppear { if readerScrolled { model.loadOlder() } }
                    }
                    ForEach(Array(model.state.items.enumerated()), id: \.element.id) { index, item in
                        MessageRow(
                            model: model, item: item, onTool: onTool, onSelect: onSelect, onEdit: onEdit,
                            onDecide: { card, approve, note in model.decide(card, approve: approve, note: note) }
                        )
                        .padding(.top, index == 0 ? 0 : Self.gap(after: model.state.items[index - 1], before: item))
                        .id(item.id)
                    }
                    ForEach(model.pending) { send in
                        PendingRow(send: send, onRetry: { model.retry(send) }, onDiscard: { model.discard(send) })
                            .padding(.top, 20)
                            .id(send.id)
                    }
                    Color.clear.frame(height: 1).id(bottomId)
                }
                .padding(.horizontal, Space.l)
                .padding(.vertical, Space.m)
                // A short chat sits at the bottom, by the composer, as a long one ends there.
                .frame(minHeight: viewport, alignment: .bottom)
            }
            .scrollDismissesKeyboard(.interactively)
            .onScrollGeometryChange(for: ScrollMetrics.self) { geometry in
                // The distance left to scroll down: what the content has beyond the visible container, less what is
                // already scrolled (the offset is measured from the top inset), and the inset at the foot (the
                // composer floats over it) is not a distance the reader has to scroll.
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
                ZStack {
                    if !atBottom {
                        GlassCircleButton(symbol: "arrow.down", label: "Jump to the newest message") {
                            atBottom = true
                            withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(bottomId, anchor: .bottom) }
                        }
                        .padding(Space.m)
                        .transition(.scale(scale: 0.6).combined(with: .opacity))
                    }
                }
                .dotAnimation(Motion.bouncy, value: atBottom)
            }
        }
    }

    /// The space above a row: steps sit close together, the owner's message stands apart, everything else is one
    /// paragraph's distance from the row above.
    private static func gap(after previous: ChatItem, before item: ChatItem) -> CGFloat {
        func isTool(_ item: ChatItem) -> Bool { if case .tool = item.kind { return true } else { return false } }
        switch item.kind {
        case .user: return 24
        case .tool: return isTool(previous) ? 0 : 10
        case .note: return 10
        default: return isTool(previous) ? 12 : 16
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

/// A message that was sent and is not yet in the chat: the owner's box, dimmed, and what to do when it did not go.
private struct PendingRow: View {
    let send: PendingSend
    let onRetry: () -> Void
    let onDiscard: () -> Void

    var body: some View {
        VStack(alignment: .trailing, spacing: Space.xs) {
            HStack(spacing: 0) {
                Spacer(minLength: 48)
                Text(send.shown)
                    .font(Theme.body).foregroundStyle(Theme.text.opacity(0.62))
                    .padding(.horizontal, Space.l).padding(.vertical, Space.m - 1)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: Radius.l + 2))
            }
            PendingStatusLine(send: send, onRetry: onRetry, onDiscard: onDiscard)
        }
    }
}

/// The one line over the composer, on glass: how long the run has worked, that it waits for the owner, or that it waits
/// for its subagents (with what the first does now). It stands outside the transcript, so it never scrolls with it.
struct StatusLine: View {
    let model: ChatModel
    let onDetails: () -> Void

    var body: some View {
        Group {
            switch model.state.line {
            case .idle:
                if case .reconnecting(_, let unauthorized) = model.connection {
                    pill(
                        symbol: unauthorized ? "key" : "arrow.triangle.2.circlepath",
                        text: unauthorized ? "This phone's key was refused. Pair again in Settings." : "Reconnecting…",
                        tone: unauthorized ? Theme.warn : Theme.muted, tint: unauthorized ? Theme.warn.opacity(0.2) : nil)
                }
            case .working(let since):
                workingPill(since: since)
            case .waitingOnYou(let count):
                pill(
                    symbol: "bell.fill", text: count == 1 ? "Waiting on you" : "Waiting on you · \(count)", tone: Theme.warn,
                    tint: Theme.warn.opacity(0.22), bounce: true)
            case .helpersInBackground(let count):
                HStack(spacing: Space.s) {
                    Image(systemName: "circle.dashed").font(.system(size: 12)).foregroundStyle(Theme.muted)
                    Text(count == 1 ? "1 subagent keeps working in the background" : "\(count) subagents keep working in the background")
                        .font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(2)
                    Spacer(minLength: Space.xs)
                    Button("Details", action: onDetails).font(Theme.meta.weight(.semibold)).foregroundStyle(Theme.accent)
                }
                .padding(.horizontal, Space.m).frame(minHeight: 36)
                .dotGlass(in: RoundedRectangle(cornerRadius: Radius.l))
            case .waitingOnSubagents(let count, let now):
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: Space.s) {
                        Image(systemName: "circle.dashed").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.accent)
                            .symbolEffect(.pulse, options: .repeating)
                        Text(count == 1 ? "Waiting on 1 subagent" : "Waiting on \(count) subagents")
                            .font(Theme.small.weight(.semibold)).foregroundStyle(Theme.text)
                        Spacer(minLength: Space.xs)
                        Button("Details", action: onDetails).font(Theme.meta.weight(.semibold)).foregroundStyle(Theme.accent)
                            .frame(minHeight: 32)
                        Button("Stop") { model.stopRun() }.font(Theme.meta.weight(.semibold)).foregroundStyle(Theme.muted)
                            .frame(minHeight: 32)
                    }
                    if let now {
                        Text(now).font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(1).padding(.leading, 21)
                    }
                }
                .padding(.horizontal, Space.m).padding(.vertical, Space.s)
                .dotGlass(in: RoundedRectangle(cornerRadius: Radius.l))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dotAnimation(Motion.snappy, value: lineKey)
    }

    private var lineKey: String {
        switch model.state.line {
        case .idle: "idle"
        case .working: "working"
        case .waitingOnYou(let count): "waiting\(count)"
        case .waitingOnSubagents(let count, _): "subagents\(count)"
        case .helpersInBackground(let count): "background\(count)"
        }
    }

    private func workingPill(since: Double?) -> some View {
        HStack(spacing: Space.s) {
            Image(systemName: "waveform")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .symbolEffect(.variableColor.iterative.reversing, options: .repeating)
            if let since {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text("Working · \(elapsed(since, now: context.date))")
                        .font(Theme.meta.weight(.medium).monospacedDigit()).foregroundStyle(Theme.text)
                }
            } else {
                Text("Working").font(Theme.meta.weight(.medium)).foregroundStyle(Theme.text)
            }
            if case .reconnecting = model.connection {
                Text("· Reconnecting…").font(Theme.meta).foregroundStyle(Theme.muted)
            }
        }
        .padding(.horizontal, Space.m).frame(minHeight: 32)
        .dotGlass(in: Capsule())
        .accessibilityElement(children: .combine)
    }

    private func pill(symbol: String, text: String, tone: Color, tint: Color? = nil, bounce: Bool = false) -> some View {
        HStack(spacing: Space.s) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).foregroundStyle(tone)
                .symbolEffect(.bounce, options: .repeat(2), isActive: bounce)
            Text(text).font(Theme.meta.weight(.medium)).foregroundStyle(tone == Theme.muted ? Theme.text : tone)
        }
        .padding(.horizontal, Space.m).frame(minHeight: 32)
        .dotGlass(in: Capsule(), tint: tint)
        .accessibilityElement(children: .combine)
    }

    private func elapsed(_ since: Double, now: Date) -> String {
        let seconds: Int = max(0, Int(now.timeIntervalSince1970 - since))
        return seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
    }
}

/// The queue, on glass: each message waiting to run, numbered, with an up and a down arrow to move it and a button to
/// remove it. There is no drag.
struct QueuePanel: View {
    let model: ChatModel

    var body: some View {
        let messages: [QueuedMessage] = model.queue.messages
        if !messages.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                Label(model.queue.held ? "Queued · paused" : "Queued · \(messages.count)", systemImage: model.queue.held ? "pause.circle" : "list.number")
                    .font(Theme.meta.weight(.semibold)).foregroundStyle(Theme.muted)
                    .padding(.horizontal, Space.m).padding(.top, Space.s + 2).padding(.bottom, 2)
                ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                    HStack(spacing: 2) {
                        Text("\(index + 1)").font(Theme.meta.monospacedDigit()).foregroundStyle(Theme.faint).frame(width: 18)
                        Text(message.text).font(Theme.small).foregroundStyle(Theme.text).lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        arrow("chevron.up", label: "Move up", enabled: index > 0) { model.moveQueued(message.id, by: -1) }
                        arrow("chevron.down", label: "Move down", enabled: index < messages.count - 1) { model.moveQueued(message.id, by: 1) }
                        arrow("xmark", label: "Remove", enabled: true) { model.removeQueued(message.id) }
                    }
                    .padding(.leading, Space.m).padding(.trailing, 2)
                }
            }
            .padding(.bottom, Space.xs)
            .dotGlass(in: RoundedRectangle(cornerRadius: Radius.xl - 2))
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .sensoryFeedback(.selection, trigger: messages.map(\.id))
        }
    }

    private func arrow(_ symbol: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold))
                .foregroundStyle(enabled ? Theme.muted : Theme.faint.opacity(0.45))
                .frame(width: 40, height: 40)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

/// The composer, one floating bar of glass: + (a photo), the text, the microphone (dictation on the phone), and a
/// button that is Send, or Queue while a run works, or Stop with an empty box. The button changes by morphing its symbol.
struct Composer: View {
    @Bindable var model: ChatModel
    @State private var picks: [PhotosPickerItem] = []
    @State private var sent: Int = 0
    @State private var stopped: Int = 0
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: Space.s) {
            DictationLine(dictation: model.dictation)
            if !model.images.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.s) {
                        ForEach(model.images) { image in
                            Image(uiImage: image.preview).resizable().scaledToFill().frame(width: 52, height: 52)
                                .clipShape(RoundedRectangle(cornerRadius: Radius.m - 2))
                        }
                    }
                    .padding(.horizontal, Space.m)
                }
            }
            HStack(alignment: .bottom, spacing: 2) {
                PhotosPicker(selection: $picks, maxSelectionCount: 4, matching: .images) {
                    Image(systemName: "plus").font(.system(size: 17, weight: .medium)).foregroundStyle(Theme.muted)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Attach a photo")
                TextField(model.isWorking ? "Queue a message" : "Message Dot", text: $model.draft, axis: .vertical)
                    .font(Theme.body)
                    .lineLimit(1...6)
                    .focused($focused)
                    .padding(.vertical, Space.m - 1)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("composer-field")
                MicButton(dictation: model.dictation) { model.toggleDictation() }
                actionButton
            }
            .padding(.horizontal, Space.xs)
        }
        .padding(.top, model.images.isEmpty && !model.dictation.state.isActive ? 0 : Space.s)
        .dotGlass(in: RoundedRectangle(cornerRadius: Radius.xl + 2), interactive: false)
        .onChange(of: model.draft) { model.draftChanged() }
        .onChange(of: picks) {
            let chosen: [PhotosPickerItem] = picks
            picks = []
            Task { await model.pick(chosen) }
        }
        .sensoryFeedback(.impact(weight: .light), trigger: sent)
        .sensoryFeedback(.impact(weight: .medium), trigger: stopped)
    }

    private var showsStop: Bool { model.isWorking && !model.canSend }

    /// One round button for all three: the arrow, the queue mark, the stop square. Its symbol is replaced in place.
    private var actionButton: some View {
        Button {
            if showsStop {
                stopped += 1
                model.stopRun()
            } else if model.canSend {
                sent += 1
                model.send()
            }
        } label: {
            Image(systemName: showsStop ? "stop.fill" : (model.isWorking ? "text.append" : "arrow.up"))
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(showsStop ? Theme.background : (model.canSend ? Theme.onAccent : Theme.faint))
                .contentTransition(.symbolEffect(.replace))
                .frame(width: 36, height: 36)
                .background {
                    Circle().fill(
                        showsStop ? AnyShapeStyle(Theme.text) : (model.canSend ? AnyShapeStyle(Theme.accentGradient) : AnyShapeStyle(Theme.faint.opacity(0.16))))
                }
                .frame(width: 44, height: 44)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .disabled(!showsStop && !model.canSend)
        .accessibilityIdentifier(showsStop ? "stop" : "send")
        .accessibilityLabel(showsStop ? "Stop" : (model.isWorking ? "Queue" : "Send"))
        .dotAnimation(Motion.snappy, value: showsStop)
        .dotAnimation(Motion.snappy, value: model.canSend)
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
                    Label(
                        model.olderFailed ? "Could not read older messages. Tap to try again." : "Load older messages",
                        systemImage: model.olderFailed ? "exclamationmark.circle" : "arrow.up.to.line")
                        .font(Theme.meta).foregroundStyle(model.olderFailed ? Theme.warn : Theme.muted)
                        .padding(.horizontal, Space.m).frame(minHeight: 32)
                        .background(Theme.surface, in: Capsule())
                        .overlay(Capsule().strokeBorder(Theme.hairline, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("load-older")
            }
            Spacer()
        }
        .frame(minHeight: 44)
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
        VStack(alignment: .leading, spacing: Space.xs) {
            ChatRow(item: item, onDecide: onDecide, onTool: onTool)
            if !standing.isEmpty {
                HStack(spacing: 4) {
                    ForEach(standing, id: \.self) { name in
                        Text(Reaction.symbol(of: name)).font(.footnote)
                            .padding(.horizontal, Space.s).frame(minHeight: 26)
                            .background(Theme.raised, in: Capsule())
                            .overlay(Capsule().strokeBorder(Theme.hairline, lineWidth: 1))
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
                Text(text).font(Theme.body).foregroundStyle(Theme.text).textSelection(.enabled).lineSpacing(4)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(Space.l)
            }
            .dotScreenBackground(inSheet: true)
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
                VStack(alignment: .leading, spacing: Space.l) {
                    HStack(spacing: Space.m) {
                        Image(systemName: ToolGlyph.symbol(for: row.tool))
                            .font(.system(size: 15, weight: .semibold)).foregroundStyle(tone)
                            .frame(width: 36, height: 36)
                            .background(tone.opacity(0.14), in: Circle())
                        VStack(alignment: .leading, spacing: 1) {
                            Text(row.tool.replacingOccurrences(of: "_", with: " ").capitalizedFirst)
                                .font(.headline).foregroundStyle(Theme.text)
                            Text(stateWords).font(Theme.meta).foregroundStyle(row.state == .failed ? Theme.bad : Theme.faint)
                        }
                        Spacer()
                        if let seconds: Double = detail?.seconds {
                            Text(seconds < 10 ? String(format: "%.1f s", seconds) : "\(Int(seconds)) s")
                                .font(Theme.meta.monospacedDigit()).foregroundStyle(Theme.faint)
                        }
                    }
                    if let target: String = detail?.target {
                        Text(target).font(Theme.mono).foregroundStyle(Theme.muted).lineLimit(3)
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
                        DotEmptyState(
                            symbol: "wifi.exclamationmark", title: "Dot could not be reached",
                            message: "The step's detail is read from your Mac.", tone: Theme.warn, actionTitle: "Try again",
                            action: { Task { await load() } })
                    } else {
                        ProgressView().controlSize(.small).tint(Theme.muted)
                    }
                }
                .padding(Space.l)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .dotScreenBackground(inSheet: true)
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
        VStack(alignment: .leading, spacing: Space.s - 2) {
            SectionLabel(title: title)
            Text(clip.text.isEmpty ? empty : clip.text)
                .font(Theme.mono).foregroundStyle(clip.text.isEmpty ? Theme.faint : Theme.text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Space.m)
                .card(fill: Theme.codeFill)
            if clip.isCut {
                Text("\(clip.omitted) more characters are not shown. The web UI shows the whole step.")
                    .font(Theme.meta).foregroundStyle(Theme.faint)
            }
        }
    }

    private var stateWords: String {
        switch row.state {
        case .running: "Running"
        case .ok: "Done"
        case .failed: "Failed"
        }
    }

    private var tone: Color { row.state == .failed ? Theme.bad : (row.state == .running ? Theme.accent : Theme.muted) }
}
