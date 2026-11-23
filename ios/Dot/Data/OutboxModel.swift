import Foundation
import Observation

/// The outbox as the screens read it: what is waiting for the Mac, drawn the moment it is made (before the actor has
/// written it), and what the daemon answered. One per store.
@MainActor
@Observable
final class OutboxModel {
    private(set) var items: [OutboxItem]
    @ObservationIgnored let box: Outbox
    /// Items made here and not yet seen in the actor's own list: shown at once, dropped when the actor has them.
    @ObservationIgnored private var optimistic: [OutboxItem] = []
    @ObservationIgnored private var watching: Task<Void, Never>?

    init(box: Outbox, initial: [OutboxItem]) {
        self.box = box
        self.items = initial
    }

    func watch() {
        guard watching == nil else { return }
        watching = Task { [weak self, box] in
            for await snapshot in await box.changes() {
                guard let self else { return }
                self.take(snapshot)
            }
        }
    }

    func stop() {
        watching?.cancel()
        watching = nil
    }

    private func take(_ snapshot: [OutboxItem]) {
        let known: Set<String> = Set(snapshot.map(\.id))
        optimistic.removeAll { known.contains($0.id) }
        let next: [OutboxItem] = snapshot + optimistic
        if next != items { items = next }
    }

    func items(for thread: String) -> [OutboxItem] { items.filter { $0.thread == thread } }

    /// Keeps a doing and sends it as soon as the link allows. The doing shows in `items` at once.
    @discardableResult
    func enqueue(_ action: OutboxItem.Action, images: [Data] = []) -> String {
        let id: String = newMessageKey()
        let item: OutboxItem = OutboxItem(id: id, action: action, createdAt: Date().timeIntervalSince1970)
        optimistic.append(item)
        items.append(item)
        Task { [box] in await box.enqueue(action, id: id, images: images) }
        return id
    }

    func retry(_ id: String) { Task { [box] in await box.retry(id) } }

    /// Takes a doing back (Edit); what it held, for the box.
    func discard(_ id: String) async -> (item: OutboxItem, photos: [Data])? {
        optimistic.removeAll { $0.id == id }
        items.removeAll { $0.id == id }
        guard let removed: OutboxItem = await box.discard(id) else { return nil }
        let photos: [Data] = await box.photos(of: removed)
        await box.forgetPhotos(of: removed)
        return (removed, photos)
    }

    func start() { Task { [box] in await box.start() } }
    func suspend() { Task { [box] in await box.suspend() } }

    func clear() async {
        optimistic = []
        items = []
        await box.clear()
    }
}
