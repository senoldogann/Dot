import Foundation
import Network
import os

/// What the phone knows of its link: whether it reaches anything, and whether the system asks apps to spend little on
/// it (Low Data Mode is `constrained`, a cellular or hotspot link is `expensive`).
struct LinkState: Sendable, Equatable {
    var online: Bool = true
    var constrained: Bool = false
    var expensive: Bool = false

    static let up: LinkState = LinkState()
    static let down: LinkState = LinkState(online: false)

    /// How many chats are read ahead of the owner's tap: none on a link the system asks to spare, a few on one that costs,
    /// the usual number otherwise (item 1371).
    var prefetchBudget: Int {
        guard online else { return 0 }
        if constrained { return 0 }
        return expensive ? 3 : 6
    }
}

/// The phone's link, watched. One per store; `report` is what the system's path monitor calls and what a test calls to
/// play a link that goes and comes back. It holds no timer and wakes only when the system says the path changed.
final class LinkMonitor: @unchecked Sendable {
    private let lock: NSLock = NSLock()
    private var current: LinkState
    private var watchers: [UUID: AsyncStream<LinkState>.Continuation] = [:]
    private var waiters: [UUID: CheckedContinuation<Bool, Never>] = [:]
    private var monitor: NWPathMonitor?
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "link")

    /// A monitor that is told by `report` alone (tests), or one that listens to the system when `watching`.
    init(initial: LinkState = .up, watching: Bool = false) {
        current = initial
        if watching { startWatching() }
    }

    deinit { monitor?.cancel() }

    var state: LinkState {
        lock.lock()
        defer { lock.unlock() }
        return current
    }

    /// Changes of the link, the present state first.
    func updates() -> AsyncStream<LinkState> {
        let (stream, continuation): (AsyncStream<LinkState>, AsyncStream<LinkState>.Continuation) = AsyncStream.makeStream(
            of: LinkState.self, bufferingPolicy: .bufferingNewest(4))
        let id: UUID = UUID()
        lock.lock()
        watchers[id] = continuation
        let now: LinkState = current
        lock.unlock()
        continuation.yield(now)
        continuation.onTermination = { [weak self] _ in
            self?.lock.lock()
            self?.watchers[id] = nil
            self?.lock.unlock()
        }
        return stream
    }

    func report(_ next: LinkState) {
        lock.lock()
        guard next != current else {
            lock.unlock()
            return
        }
        let wasOnline: Bool = current.online
        current = next
        let tell: [AsyncStream<LinkState>.Continuation] = Array(watchers.values)
        var wake: [CheckedContinuation<Bool, Never>] = []
        if next.online, !wasOnline {
            wake = Array(waiters.values)
            waiters = [:]
        }
        lock.unlock()
        for watcher in tell { watcher.yield(next) }
        for waiter in wake { waiter.resume(returning: true) }
    }

    /// Waits until the link is up, at most `timeout`; true when it is. A link that is up answers at once.
    func waitForOnline(timeout: Duration) async -> Bool {
        if state.online { return true }
        let id: UUID = UUID()
        return await withTaskGroup(of: Bool.self) { group in
            group.addTask { [self] in
                await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                    lock.lock()
                    if current.online {
                        lock.unlock()
                        continuation.resume(returning: true)
                    } else {
                        waiters[id] = continuation
                        lock.unlock()
                    }
                }
            }
            group.addTask {
                try? await Task.sleep(for: timeout)
                return false
            }
            let first: Bool = await group.next() ?? false
            group.cancelAll()
            // A timer that won leaves the first task's continuation behind: it is let go with a no.
            let left: CheckedContinuation<Bool, Never>? = lock.withLock { waiters.removeValue(forKey: id) }
            left?.resume(returning: false)
            return first
        }
    }

    private func startWatching() {
        #if DEBUG
        // `-DotLinkFile <path>`: the file's word, `down` or `up`, is the link, read every half second; for the simulator, which
        // shares the Mac's network and cannot be taken off it. The system's own path is then not listened to.
        if let path: String = UserDefaults.standard.string(forKey: "DotLinkFile") {
            watchFile(path)
            return
        }
        #endif
        let monitor: NWPathMonitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let state: LinkState = LinkState(
                online: path.status == .satisfied, constrained: path.isConstrained, expensive: path.isExpensive)
            self?.report(state)
        }
        monitor.start(queue: DispatchQueue(label: "dev.dotagent.phone.link", qos: .utility))
        self.monitor = monitor
    }

    #if DEBUG
    private var fileTimer: DispatchSourceTimer?

    private func watchFile(_ path: String) {
        let timer: DispatchSourceTimer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "dev.dotagent.phone.linkfile"))
        timer.schedule(deadline: .now(), repeating: .milliseconds(400))
        timer.setEventHandler { [weak self] in
            let word: String = ((try? String(contentsOfFile: path, encoding: .utf8)) ?? "up").trimmingCharacters(in: .whitespacesAndNewlines)
            self?.report(word == "down" ? .down : .up)
        }
        timer.resume()
        fileTimer = timer
    }
    #endif

    func stop() {
        #if DEBUG
        fileTimer?.cancel()
        fileTimer = nil
        #endif
        monitor?.cancel()
        monitor = nil
        lock.lock()
        let tell: [AsyncStream<LinkState>.Continuation] = Array(watchers.values)
        watchers = [:]
        let wake: [CheckedContinuation<Bool, Never>] = Array(waiters.values)
        waiters = [:]
        lock.unlock()
        for watcher in tell { watcher.finish() }
        for waiter in wake { waiter.resume(returning: false) }
    }
}
