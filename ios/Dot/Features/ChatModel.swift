import Foundation
import Observation
import PhotosUI
import SwiftUI
import UIKit

/// A message sent from here that the daemon has not yet taken.
struct PendingSend: Identifiable, Equatable {
    let id: String
    let text: String
    var failed: Bool = false
}

/// An image picked for the next message.
struct PickedImage: Identifiable {
    let id: UUID = UUID()
    let name: String
    let data: Data
    let preview: UIImage
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
    private(set) var pending: [PendingSend] = []
    private(set) var notice: String?
    /// True when the chat was drawn from what was held before the network answered (no spinner is shown for it).
    let wasCached: Bool
    private(set) var loaded: Bool = false
    var draft: String = ""
    var images: [PickedImage] = []

    @ObservationIgnored private let token: UUID = UUID()
    @ObservationIgnored private var session: ChatSession?
    @ObservationIgnored private var reading: Task<Void, Never>?

    init(thread: ThreadSummary, store: NativeStore) {
        self.threadId = thread.id
        self.store = store
        self.title = thread.title
        let warm: ChatState? = store.warmState(for: thread.id)
        self.state = warm ?? ChatState()
        self.wasCached = warm != nil
        self.loaded = warm != nil
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
        Task { await refreshQueue() }
        Task { await refreshAutomations() }
    }

    func stop() {
        reading?.cancel()
        reading = nil
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
            connection = feedState
        case .run(let record, let name):
            run = record
            if !name.isEmpty { title = name }
        case .failed:
            loaded = true
        case .lifecycle(let kind):
            if ["queue_changed", "run_finished", "run_queued"].contains(kind) { Task { await refreshQueue() } }
            if kind == "run_finished" || kind == "run_queued" { Task { await refreshAutomations() } }
        }
    }

    // MARK: Reading beside the transcript

    func refreshQueue() async {
        if let fresh: QueueView = try? await store.api.queue(thread: threadId) { queue = fresh }
    }

    func refreshAutomations() async {
        if let fresh: [ThreadAutomation] = try? await store.api.automations(thread: threadId) { automations = fresh }
    }

    // MARK: Doing

    /// Sends the draft: it starts a run when none works here, and is queued behind the one that does.
    func send() {
        let text: String = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend else { return }
        let key: String = newMessageKey()
        let shown: String = text.isEmpty ? "Photo" : text
        let sending: [PickedImage] = images
        draft = ""
        images = []
        pending.append(PendingSend(id: key, text: shown))
        Task { await deliver(key: key, text: text.isEmpty ? "(photo)" : text, images: sending) }
    }

    func retry(_ send: PendingSend) {
        guard let at: Int = pending.firstIndex(of: send) else { return }
        pending[at].failed = false
        Task { await deliver(key: send.id, text: send.text, images: []) }
    }

    func discard(_ send: PendingSend) {
        pending.removeAll { $0.id == send.id }
        if draft.isEmpty { draft = send.text }
    }

    private func deliver(key: String, text: String, images: [PickedImage]) async {
        do {
            var ids: [String] = []
            let uploader: DaemonClient = DaemonClient(session: store.api.session, maxAttempts: 2)
            for image in images {
                let upload: AttachmentUpload = AttachmentUpload(
                    name: image.name, mediaType: "image/jpeg", dataB64: image.data.base64EncodedString())
                ids.append(try await uploader.uploadAttachment(upload, host: store.host, key: store.api.key))
            }
            let shown: QueueView = try await store.api.send(
                message: Message(text: text, key: key, model: nil, attachments: ids), thread: threadId)
            queue = shown
            if shown.besideThreadId != nil { notice = "Started in a chat of its own." }
            await session?.refresh()
            pending.removeAll { $0.id == key }
        } catch {
            if let at: Int = pending.firstIndex(where: { $0.id == key }) { pending[at].failed = true }
        }
    }

    func stopRun() {
        guard let id: String = state.newestRunId else { return }
        Task {
            do {
                try await store.api.stop(run: id)
                await session?.stopped(run: id)
            } catch {
                notice = "Could not stop the run."
            }
        }
    }

    func decide(_ card: ApprovalCard, approve: Bool, note: String = "") {
        Task {
            await session?.answered(approval: card.approvalId, status: approve ? "approved" : "denied", runId: card.runId)
            do {
                try await store.api.decide(approval: card.approvalId, approve: approve, note: note)
            } catch {
                notice = "Your answer did not reach Dot. Try again."
                await session?.refresh()
            }
        }
    }

    // MARK: Queue

    /// Moves a queued message one place up (`-1`) or down (`+1`): the list changes at once and the daemon is told the
    /// new order; the daemon's own answer, or its queue when it refuses, is what stays.
    func moveQueued(_ id: String, by step: Int) {
        guard let order: [String] = queueOrder(queue.messages.map(\.id), moving: id, by: step) else { return }
        let before: QueueView = queue
        var moved: [QueuedMessage] = []
        for (position, orderedId) in order.enumerated() {
            if var message: QueuedMessage = queue.messages.first(where: { $0.id == orderedId }) {
                message.position = position
                moved.append(message)
            }
        }
        queue.messages = moved
        Task {
            do {
                queue = try await store.api.reorderQueue(thread: threadId, ids: order)
            } catch {
                queue = before
                notice = "Could not move the message."
                await refreshQueue()
            }
        }
    }

    func removeQueued(_ id: String) {
        let before: QueueView = queue
        queue.messages.removeAll { $0.id == id }
        Task {
            do {
                queue = try await store.api.removeQueued(id)
            } catch {
                queue = before
                notice = "Could not remove the message."
            }
        }
    }

    // MARK: Schedules of the chat

    func setAutomation(_ id: String, on: Bool) {
        if let at: Int = automations.firstIndex(where: { $0.id == id }) { automations[at].schedule.record.enabled = on }
        Task {
            do {
                _ = try await store.api.setSchedule(id, on: on)
            } catch {
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

    /// The model of the newest run, in words ("opus"), for the head.
    var modelLabel: String? { run?.model.split(separator: ":").last.map(String.init) }
}
