import Foundation
import Network
import os

/// What the device knows of its link: whether it reaches anything, and over what (a change of interface, Wi-Fi to
/// cellular, kills the open sockets even while `online` stays true).
public struct LinkState: Sendable, Equatable {
    public var online: Bool
    public var interface: String

    public init(online: Bool, interface: String) {
        self.online = online
        self.interface = interface
    }

    public static let up: LinkState = LinkState(online: true, interface: "wifi")
    public static let down: LinkState = LinkState(online: false, interface: "none")
}

/// The device's link, watched; moved from the old app's `LinkMonitor` with the interface added. `report` is what the
/// system's path monitor calls and what a test calls to play a link that goes, comes back or changes interface.
public final class LinkWatcher: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var current: LinkState
    private var watchers: [UUID: AsyncStream<LinkState>.Continuation] = [:]
    private var monitor: NWPathMonitor?

    /// A watcher told by `report` alone (tests).
    public init(initial: LinkState) {
        current = initial
    }

    /// A watcher that listens to the system's path.
    public static func system() -> LinkWatcher {
        let watcher: LinkWatcher = LinkWatcher(initial: .up)
        let monitor: NWPathMonitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak watcher] path in
            let interface: String =
                path.usesInterfaceType(.wifi) ? "wifi"
                : path.usesInterfaceType(.cellular) ? "cellular" : path.usesInterfaceType(.wiredEthernet) ? "wired" : "other"
            watcher?.report(LinkState(online: path.status == .satisfied, interface: path.status == .satisfied ? interface : "none"))
        }
        monitor.start(queue: DispatchQueue(label: "dev.dotagent.phone.next.link", qos: .utility))
        watcher.monitor = monitor
        return watcher
    }

    deinit { monitor?.cancel() }

    public var state: LinkState { lock.withLock { current } }

    /// Changes of the link, the present state first.
    public func updates() -> AsyncStream<LinkState> {
        let (stream, continuation): (AsyncStream<LinkState>, AsyncStream<LinkState>.Continuation) = AsyncStream.makeStream(
            of: LinkState.self, bufferingPolicy: .bufferingNewest(8))
        let id: UUID = UUID()
        let now: LinkState = lock.withLock {
            watchers[id] = continuation
            return current
        }
        continuation.yield(now)
        continuation.onTermination = { [weak self] _ in
            self?.lock.withLock { self?.watchers[id] = nil }
        }
        return stream
    }

    public func report(_ next: LinkState) {
        let tell: [AsyncStream<LinkState>.Continuation] = lock.withLock {
            guard next != current else { return [] }
            current = next
            return Array(watchers.values)
        }
        for watcher in tell { watcher.yield(next) }
    }
}

/// A wait that ends at its time or when it is woken (the app came back, the link came back), whichever is first.
public actor Waker {
    private var generation: Int = 0
    private var parked: [UUID: CheckedContinuation<Void, Never>] = [:]

    public init() {}

    /// Wakes every wait under way, and a wait that starts from a generation before this one.
    public func wake() {
        generation += 1
        let all: [CheckedContinuation<Void, Never>] = Array(parked.values)
        parked = [:]
        for one in all { one.resume() }
    }

    public var current: Int { generation }

    private func release(_ id: UUID) { parked.removeValue(forKey: id)?.resume() }

    /// Returns once a wake after `seen` came, or the task is cancelled.
    private func woken(after seen: Int) async {
        let id: UUID = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                if generation > seen || Task.isCancelled {
                    continuation.resume()
                } else {
                    parked[id] = continuation
                }
            }
        } onCancel: {
            Task { await self.release(id) }
        }
    }

    /// Waits `seconds` on `clock`, or until a wake after generation `seen` (read it when the wait was decided, so a wake
    /// in between is not lost).
    public nonisolated func wait(seconds: Double, clock: any EngineClock, seen: Int) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.woken(after: seen) }
            group.addTask {
                do {
                    try await clock.sleep(seconds: seconds)
                } catch {
                    // Cancelled: the wake came first.
                }
            }
            await group.next()
            group.cancelAll()
        }
    }
}
