import Foundation
import Observation
import PhotosUI
import SwiftUI
import UIKit
import os

/// A message sent from here that the daemon has not yet taken: it waits in the outbox (on disk) and goes, in order,
/// when the link allows.
struct PendingSend: Identifiable, Equatable {
    let id: String
    /// What the owner typed (empty for a photo alone), which Edit puts back in the box.
    let typed: String
    /// What the owner sees in the bubble.
    let shown: String
    var phase: OutboxItem.Phase
    /// What to tell the owner when it did not go (the daemon's words, or that it waited too long).
    var note: String?

    /// Not sent and will not be sent by itself: Retry or Edit.
    var failed: Bool { phase == .failed }
    /// Not sent yet (the link is down, or it is not its turn): it goes by itself.
    var waiting: Bool { phase == .waiting }
    var sending: Bool { phase == .sending }
}

/// An image picked for the next message.
struct PickedImage: Identifiable, Equatable {
    let id: UUID = UUID()
    let name: String
    let data: Data
    let preview: UIImage

    static func == (left: PickedImage, right: PickedImage) -> Bool { left.id == right.id }
}

/// An open chat: its state (drawn from the disk or the warm copy in its first frame, then kept up to date by its
/// session), its queue, its schedules, and what the owner does here: send, stop, answer, move a queued message.
@MainActor
@Observable
final class ChatModel {
    let threadId: String
    let store: NativeStore
    private(set) var state: ChatState
    private(set) var title: String
    private(set) var connection: FeedState = .connecting
    private(set) var queue: QueueView = QueueView(held: false, messages: [], besideRunId: nil, besideThreadId: nil)
    private(set) var automations: [ThreadAutomation] = []
    private(set) var run: RunRecord?
    /// What the owner sent from here that the Mac has not yet taken, oldest first (the outbox's messages of this chat).
    var pending: [PendingSend] {
        store.outbox.items(for: threadId).compactMap { item in
            guard case .message(_, _, let typed, let shown, _, _, _) = item.action else { return nil }
            return PendingSend(id: item.id, typed: typed, shown: shown, phase: item.phase, note: item.note)
        }
    }
    private(set) var notice: String?
    /// What the chat's next message starts with (model, reasoning level, mode); `nil` until the daemon has told.
    private(set) var choices: ChatChoices?
    /// Whether the daemon may hold runs older than the rows shown, and whether a page of them is being read or failed.
    private(set) var hasOlder: Bool = false
    private(set) var loadingOlder: Bool = false
    private(set) var olderFailed: Bool = false
    /// The row that was first before older ones were put above it: the transcript scrolls back to it to keep its place.
    private(set) var olderAnchor: String?
    private(set) var reactions: [ReactionRecord] = []
    /// Set when an edit rewound the chat to nothing: its words went to a new chat's box, and this chat has no more to show.
    private(set) var leaves: Bool = false
    let dictation: Dictation = Dictation()
    /// True when the chat was drawn from what was held before the network answered (no spinner is shown for it).
    let wasCached: Bool
    private(set) var loaded: Bool = false
    var draft: String = ""
    var images: [PickedImage] = []

    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "chat-screen")
    /// The cards whose answer is on its way: pressed once, not twice.
    @ObservationIgnored private var answering: Set<String> = []
    @ObservationIgnored private let token: UUID = UUID()
    @ObservationIgnored private var session: ChatSession?
    @ObservationIgnored private var reading: Task<Void, Never>?
    @ObservationIgnored private var outcomes: Task<Void, Never>?
    @ObservationIgnored private var beside: [Task<Void, Never>] = []

    init(thread: ThreadSummary, store: NativeStore) {
        self.threadId = thread.id
        self.store = store
        self.title = thread.title
        let warm: ChatState? = store.warmState(for: thread.id)
        self.state = warm ?? ChatState()
        self.wasCached = warm != nil
        self.loaded = warm != nil
        self.draft = store.drafts.text(for: thread.id)
    }

    var isWorking: Bool { state.isWorking }
    var canSend: Bool { !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !images.isEmpty }

    // MARK: Life

    func start() {
        guard reading == nil else { return }
        let session: ChatSession = store.makeSession(for: threadId, token: token)
        self.session = session
        store.markSeen(threadId)
        reading = Task { [weak self] in
            let outputs: AsyncStream<ChatOutput> = await session.start()
            for await output in outputs {
                guard let self else { return }
                self.take(output)
            }
        }
        outcomes = Task { [weak self, box = store.outbox.box] in
            for await outcome in await box.outcomes() {
                guard let self else { return }
                await self.take(outcome)
            }
        }
        beside = [
            Task { [weak self] in await self?.refreshQueue() },
            Task { [weak self] in await self?.refreshAutomations() },
            Task { [weak self] in await self?.refreshReactions() },
        ]
    }

    func stop() {
        dictation.stop()
        store.drafts.flush()
        reading?.cancel()
        reading = nil
        outcomes?.cancel()
        outcomes = nil
        // What was being read for this screen ends with it.
        for task in beside { task.cancel() }
        beside = []
        store.keep(state, for: threadId)
        store.release(token: token)
        session = nil
    }

    private func take(_ output: ChatOutput) {
        switch output {
        case .state(let next):
            state = next
            loaded = true
            store.keep(next, for: threadId)
        case .connection(let feedState):
            // With no link the line over the screens says so: the chat does not add "reconnecting" to it.
            if case .reconnecting(_, false) = feedState, !store.link.state.online { connection = .connecting } else { connection = feedState }
        case .run(let record, let name, let kept):
            run = record
            choices = kept
            if !name.isEmpty { title = name }
        case .older(let more):
            hasOlder = more
        case .failed(let words):
            loaded = true
            // What the phone shows may be behind the daemon: say so rather than draw it as current.
            logger.info("the chat could not be brought up to date: \(words, privacy: .public)")
            notice = words.hasPrefix("A message") ? words : "Dot could not be reached. This is what the phone held."
        case .lifecycle(let kind):
            if ["queue_changed", "run_finished", "run_queued"].contains(kind) { Task { await refreshQueue() } }
            if kind == "run_finished" || kind == "run_queued" { Task { await refreshAutomations() } }
            if kind == "reaction_added" || kind == "reaction_removed" { Task { await refreshReactions() } }
        case .aside(let held, let schedules, let marks):
            // What the disk held beside the transcript, drawn until the network says better.
            if let held, queue.messages.isEmpty { queue = held }
            if let schedules, automations.isEmpty { automations = schedules }
            if let marks, reactions.isEmpty { reactions = marks }
        }
    }

    /// What the Mac answered to something the owner did from here that had to wait (or did not).
    private func take(_ outcome: OutboxOutcome) async {
        switch outcome {
        case .delivered(let item, let shown):
            guard item.thread == threadId else { return }
            queue = shown
            if shown.besideThreadId != nil { notice = "Started in a chat of its own." }
            await session?.refresh()
        case .answered(let item, let status):
            guard item.thread == threadId, case .decision(let approval, let run, _, _, _) = item.action else { return }
            answering.remove(approval)
            await session?.answered(approval: approval, status: status, runId: run)
        case .expired(let item, let status):
            guard item.thread == threadId, case .decision(let approval, let run, _, _, _) = item.action else { return }
            answering.remove(approval)
            await session?.answered(approval: approval, status: status, runId: run)
            await session?.refresh()
            notice = status == "expired"
                ? "That question had expired when your answer reached Dot, so it was not used."
                : "That question was already answered, so your answer was not used."
        case .done(let item):
            guard item.thread == threadId else { return }
            switch item.action {
            case .stop(let run, _): await session?.stopped(run: run)
            default: await refreshQueue()
            }
        case .refused(let item, let words):
            guard item.thread == threadId else { return }
            if case .decision(let approval, _, _, _, _) = item.action { answering.remove(approval) }
            if item.isMessage { return }
            notice = words
            await refreshQueue()
            _ = await session?.refresh()
        }
    }

    // MARK: Reading beside the transcript

    func refreshQueue() async {
        do {
            queue = try await store.api.queue(thread: threadId)
            await session?.keep(queue: queue, automations: nil, reactions: nil)
        } catch {
            logger.error("the queue could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    func refreshAutomations() async {
        do {
            automations = try await store.api.automations(thread: threadId)
            await session?.keep(queue: nil, automations: automations, reactions: nil)
        } catch {
            logger.error("the schedules could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    // MARK: Doing

    /// Sends the draft: it goes to the outbox (on disk, with the key the daemon starts it once by) and shows at once as
    /// waiting; it is sent in its turn as soon as the link allows, and starts a run when none works here or is queued
    /// behind the one that does.
    func send() {
        let text: String = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend else { return }
        let shown: String = text.isEmpty ? "Photo" : text
        let sending: [PickedImage] = images
        draft = ""
        images = []
        store.outbox.enqueue(
            .message(
                thread: threadId, text: text.isEmpty ? "(photo)" : text, typed: text, shown: shown, choices: messageChoices,
                images: [], uploaded: []),
            images: sending.map(\.data))
    }

    func retry(_ send: PendingSend) { store.outbox.retry(send.id) }

    /// Takes a message back to the box (Edit): its words and its photos.
    func discard(_ send: PendingSend) {
        Task {
            guard let back: (item: OutboxItem, photos: [Data]) = await store.outbox.discard(send.id) else { return }
            if draft.isEmpty { draft = send.typed }
            let restored: [PickedImage] = back.photos.enumerated().compactMap { number, data in
                UIImage(data: data).map { PickedImage(name: "photo-\(number).jpg", data: data, preview: $0) }
            }
            images = restored + images
        }
    }

    func stopRun() {
        guard let id: String = state.newestRunId else { return }
        if !store.link.state.online { notice = "Dot will be told to stop when you're back online." }
        store.outbox.enqueue(.stop(run: id, thread: threadId))
    }

    /// Answers a card. It is kept (on disk) and sent when the link allows, so an answer given with no link is not lost; the
    /// card changes when the daemon took the answer (its own reply says how the card stands). An answer that comes too late
    /// (the question expired or was answered on the Mac) is said in a line.
    func decide(_ card: ApprovalCard, approve: Bool, note: String = "") {
        guard !answering.contains(card.approvalId) else { return }
        answering.insert(card.approvalId)
        if !store.link.state.online { notice = "Your answer will be sent when you're back online." }
        store.outbox.enqueue(.decision(approval: card.approvalId, run: card.runId, thread: threadId, approve: approve, note: note))
    }

    // MARK: Queue

    /// Moves a queued message one place up (`-1`) or down (`+1`): the list changes at once and the daemon is told the
    /// new order (kept and sent when the link allows); the queue is read again when it answered.
    func moveQueued(_ id: String, by step: Int) {
        guard let order: [String] = queueOrder(queue.messages.map(\.id), moving: id, by: step) else { return }
        var moved: [QueuedMessage] = []
        for (position, orderedId) in order.enumerated() {
            if var message: QueuedMessage = queue.messages.first(where: { $0.id == orderedId }) {
                message.position = position
                moved.append(message)
            }
        }
        queue.messages = moved
        store.outbox.enqueue(.reorder(thread: threadId, ids: order))
    }

    func removeQueued(_ id: String) {
        queue.messages.removeAll { $0.id == id }
        store.outbox.enqueue(.removeQueued(thread: threadId, message: id))
    }

    // MARK: Schedules of the chat

    func setAutomation(_ id: String, on: Bool) {
        if let at: Int = automations.firstIndex(where: { $0.id == id }) { automations[at].schedule.record.enabled = on }
        Task {
            do {
                _ = try await store.api.setSchedule(id, on: on)
            } catch {
                logger.error("a schedule was not switched: \(String(describing: error), privacy: .public)")
                notice = "Could not switch the schedule."
            }
            await refreshAutomations()
        }
    }

    func clearNotice() { notice = nil }

    // MARK: Images

    func pick(_ items: [PhotosPickerItem]) async {
        for item in items {
            guard let data: Data = try? await item.loadTransferable(type: Data.self), let image: UIImage = UIImage(data: data),
                let jpeg: Data = image.jpegData(compressionQuality: 0.85)
            else { continue }
            images.append(PickedImage(name: "photo-\(images.count + 1).jpg", data: jpeg, preview: image))
        }
    }

    // MARK: The chat's model and reasoning level

    /// What each message of this chat carries of the chat's choices, and the agent whose chat it is.
    var messageChoices: MessageChoices { MessageChoices(choices, agentId: run?.agentId) }

    /// Picks the model the chat's next messages run on (`nil`: the default model); the daemon keeps it with the chat, as
    /// the web's composer does. A refusal puts the old choice back.
    func choose(model ref: String?, among providers: [ProviderModels]) {
        change((choices ?? .fresh).picking(model: ref, among: providers))
    }

    func choose(effort level: String?) {
        change((choices ?? .fresh).picking(effort: level))
    }

    private func change(_ next: ChatChoices) {
        let before: ChatChoices? = choices
        choices = next
        Task {
            do {
                try await store.api.keep(choices: next, thread: threadId)
            } catch {
                logger.error("the chat's choices were not kept: \(String(describing: error), privacy: .public)")
                choices = before
                notice = "The model could not be changed."
            }
        }
    }

    // MARK: Older messages

    /// Reads a page of runs older than the first row and puts them above it, keeping the reader's place.
    func loadOlder() {
        guard hasOlder, !loadingOlder, let session else { return }
        loadingOlder = true
        olderFailed = false
        olderAnchor = state.items.first?.id
        Task {
            let outcome: OlderOutcome = await session.loadOlder()
            loadingOlder = false
            switch outcome {
            case .failed: olderFailed = true
            case .loaded(_, let exhausted): hasOlder = !exhausted
            case .nothing: break
            }
            // The anchor is kept for a moment: the rows arrive on the stream, and the screen scrolls back on their arrival.
            try? await Task.sleep(for: .milliseconds(600))
            olderAnchor = nil
        }
    }

    // MARK: A tool step's detail

    /// The arguments and the result of a tool step, read from its two events (the chat keeps only a line of each). Throws
    /// when the daemon cannot be reached.
    func detail(of row: ToolRow) async throws -> ToolDetail {
        var after: Int = max(0, row.eventId - 1)
        var call: EventRecord?
        var result: EventRecord?
        for _ in 0..<ToolEvents.pages {
            let page: EventsPage = try await store.api.events(run: row.runId, after: after, limit: ToolEvents.pageSize)
            call = call ?? ToolEvents.call(row.callId, in: page.events)
            result = result ?? ToolEvents.result(row.callId, in: page.events)
            guard result == nil, page.size >= ToolEvents.pageSize, let top: Int = page.topId else { break }
            after = top
        }
        return ToolDetail.make(row: row, call: call, result: result)
    }

    // MARK: Message actions

    func refreshReactions() async {
        do {
            reactions = try await store.api.reactions(thread: threadId)
            await session?.keep(queue: nil, automations: nil, reactions: reactions)
        } catch {
            logger.error("the reactions could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    /// Puts a reaction on what Dot said, or takes it back when it stands. What the daemon did with it (a follow-up, a
    /// hold) is said in a line.
    func toggleReaction(_ name: String, on item: ChatItem) {
        guard let target: (runId: String, message: String) = MessageActions.reactionTarget(of: item) else { return }
        let stands: Bool = MessageActions.standing(on: item, among: reactions).contains(name)
        Task {
            do {
                let outcome: ReactionOutcome = stands
                    ? try await store.api.unreact(run: target.runId, message: target.message, reaction: name)
                    : try await store.api.react(run: target.runId, message: target.message, reaction: name)
                reactions.removeAll { $0.runId == target.runId && $0.message == target.message }
                reactions.append(contentsOf: outcome.reactions)
                if !outcome.said.isEmpty { notice = outcome.said }
            } catch {
                logger.error("a reaction was not taken: \(String(describing: error), privacy: .public)")
                notice = "The reaction could not be saved."
            }
        }
    }

    /// Edit from here: the chat goes back to before this message of the owner's, the messages after it are taken out (the
    /// files they changed stay as they are), and the message's words come back to the box. A chat rewound to nothing
    /// leaves its words in a new chat's box and closes.
    func editFromHere(_ item: ChatItem) {
        guard let runId: String = MessageActions.rewindTarget(of: item, chatIsWorking: isWorking) else { return }
        Task {
            do {
                let rewound: RewindResult = try await store.api.rewind(run: runId)
                await session?.reset()
                if rewound.backTo == nil {
                    store.drafts.save(rewound.text, for: Drafts.newChatKey(agent: run?.agentId))
                    store.drafts.flush()
                    leaves = true
                } else {
                    draft = draft.isEmpty ? rewound.text : draft + "\n" + rewound.text
                }
            } catch {
                logger.error("the chat was not rewound: \(String(describing: error), privacy: .public)")
                notice = "The chat could not go back to that message."
            }
        }
    }

    // MARK: Drafts and dictation

    /// Keeps what is in the box for the next launch (an empty box keeps nothing).
    func persistDraft() { store.drafts.save(draft, for: threadId) }

    /// What the box held when dictation began, and what it wrote since: a key the owner types while it listens ends it at
    /// once (the words they typed stay), not at the next word it hears.
    @ObservationIgnored private var dictationBase: String = ""
    @ObservationIgnored private var dictationWritten: DictationWritten = DictationWritten()

    func draftChanged() {
        persistDraft()
        if dictation.state.isActive, draft != (dictationWritten.text ?? dictationBase) { dictation.stop() }
    }

    /// The microphone: listens and shows the words as they are heard after what was typed; pressed again, it stops.
    func toggleDictation() {
        let base: String = draft
        let written: DictationWritten = DictationWritten()
        dictationBase = base
        dictationWritten = written
        dictation.toggle { [weak self] heard in
            guard let self else { return }
            // Typing while it listens stops it, and what was typed stays.
            guard let next: String = DictationText.update(current: self.draft, lastWritten: written.text, base: base, heard: heard) else {
                self.dictation.stop()
                return
            }
            written.text = next
            self.draft = next
        }
    }

    /// The model of the newest run, in words ("opus"), for the head.
    var modelLabel: String? { run?.model.split(separator: ":").last.map(String.init) }
}
