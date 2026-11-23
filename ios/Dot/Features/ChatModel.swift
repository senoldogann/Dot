import Foundation
import Observation
import PhotosUI
import SwiftUI
import UIKit
import os

/// A message sent from here that the daemon has not yet taken.
struct PendingSend: Identifiable, Equatable {
    let id: String
    /// What the daemon is sent (a photo with no words is sent as "(photo)").
    let text: String
    /// What the owner typed (empty for a photo alone), which Edit puts back in the box.
    let typed: String
    /// What the owner sees in the bubble.
    let shown: String
    let images: [PickedImage]
    var failed: Bool = false
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
    private(set) var pending: [PendingSend] = []
    private(set) var notice: String?
    /// True when the chat was drawn from what was held before the network answered (no spinner is shown for it).
    let wasCached: Bool
    private(set) var loaded: Bool = false
    var draft: String = ""
    var images: [PickedImage] = []

    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "chat-screen")
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
        case .failed(let words):
            loaded = true
            // What the phone shows may be behind the daemon: say so rather than draw it as current.
            logger.info("the chat could not be brought up to date: \(words, privacy: .public)")
            notice = words.hasPrefix("A message") ? words : "Dot could not be reached. This is what the phone held."
        case .lifecycle(let kind):
            if ["queue_changed", "run_finished", "run_queued"].contains(kind) { Task { await refreshQueue() } }
            if kind == "run_finished" || kind == "run_queued" { Task { await refreshAutomations() } }
        }
    }

    // MARK: Reading beside the transcript

    func refreshQueue() async {
        do {
            queue = try await store.api.queue(thread: threadId)
        } catch {
            logger.error("the queue could not be read: \(String(describing: error), privacy: .public)")
        }
    }

    func refreshAutomations() async {
        do {
            automations = try await store.api.automations(thread: threadId)
        } catch {
            logger.error("the schedules could not be read: \(String(describing: error), privacy: .public)")
        }
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
        let sent: PendingSend = PendingSend(id: key, text: text.isEmpty ? "(photo)" : text, typed: text, shown: shown, images: sending)
        pending.append(sent)
        Task { await deliver(sent) }
    }

    func retry(_ send: PendingSend) {
        guard let at: Int = pending.firstIndex(of: send) else { return }
        pending[at].failed = false
        Task { await deliver(send) }
    }

    func discard(_ send: PendingSend) {
        pending.removeAll { $0.id == send.id }
        if draft.isEmpty { draft = send.typed }
        images = send.images + images
    }

    private func deliver(_ send: PendingSend) async {
        let key: String = send.id
        do {
            var ids: [String] = []
            let uploader: DaemonClient = DaemonClient(session: store.api.session, maxAttempts: 2)
            for image in send.images {
                let upload: AttachmentUpload = AttachmentUpload(
                    name: image.name, mediaType: "image/jpeg", dataB64: image.data.base64EncodedString())
                ids.append(try await uploader.uploadAttachment(upload, host: store.host, key: store.api.key))
            }
            let shown: QueueView = try await store.api.send(
                message: Message(text: send.text, key: key, model: nil, attachments: ids), thread: threadId)
            queue = shown
            if shown.besideThreadId != nil { notice = "Started in a chat of its own." }
            await session?.refresh()
            pending.removeAll { $0.id == key }
        } catch {
            logger.error("a message was not sent: \(String(describing: error), privacy: .public)")
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
                logger.error("the run was not stopped: \(String(describing: error), privacy: .public)")
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
                logger.error("an answer did not reach the daemon: \(String(describing: error), privacy: .public)")
                if let failure: DaemonRequestError = error as? DaemonRequestError, !failure.isRetryable {
                    // A refusal (the Mac answered it, it expired): the daemon's own list says how the card stands.
                    notice = "Dot no longer asks that."
                    await session?.refresh()
                } else {
                    // Nothing reached the daemon: the card asked and still asks, with its buttons, and the run still
                    // waits on the owner.
                    await session?.reopened(approval: card.approvalId)
                    notice = "Your answer did not reach Dot. Try again."
                }
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
                logger.error("a queued message was not moved: \(String(describing: error), privacy: .public)")
                queue = before
                notice = "Could not move the message."
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
                logger.error("a queued message was not removed: \(String(describing: error), privacy: .public)")
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

    /// The model of the newest run, in words ("opus"), for the head.
    var modelLabel: String? { run?.model.split(separator: ":").last.map(String.init) }
}
