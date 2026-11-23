import Foundation
import os

/// What a feed tells its reader.
enum FeedUpdate: Sendable {
    /// Events of the run, in order, decoded off the main actor.
    case events([EventRecord])
    /// A message that is no event of a run (the lifecycle stream's `resync`).
    case notice(String)
    /// The connection's state, for the one line that says "reconnecting".
    case state(FeedState)
}

enum FeedState: Sendable, Equatable {
    case connecting
    case live
    /// Waiting to try again after a failure; `unauthorized` is a 401/403, which more tries do not mend.
    case reconnecting(attempt: Int, unauthorized: Bool)
    case ended
}

/// The request that follows a run's events from the id `after`: the id goes both as the `after` the daemon has always
/// read and as the standard `Last-Event-ID`, so that neither a daemon that reads only one of them nor a proxy that
/// keeps only the other replays the run's history.
func runStreamRequest(api: APIClient, runId: String, after: Int) -> URLRequest {
    var request: URLRequest = api.request(
        "GET", "/api/runs/\(runId)/stream", query: [URLQueryItem(name: "after", value: String(after))])
    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
    if after > 0 { request.setValue(String(after), forHTTPHeaderField: "Last-Event-ID") }
    request.timeoutInterval = 45
    return request
}

/// The waits between attempts to connect: 0.5 s doubling to 15 s (the daemon writes a keepalive every 15 s), each
/// with up to 20 % jitter so that a phone and a Mac waking together do not step on each other.
func backoffDelay(attempt: Int, jitter: Double = Double.random(in: 0...0.2)) -> Duration {
    let seconds: Double = min(15, 0.5 * pow(2, Double(max(0, attempt - 1))))
    return .milliseconds(Int(seconds * (1 + jitter) * 1000))
}

/// One event stream of the daemon, kept open: it goes on after the last event id it read (as `after` and as
/// `Last-Event-ID`, so no history is read again), tries again with a growing wait when it drops, and tries again at
/// once when told the app is back in front or the network changed (`kick`). It ends when `isFinal` says an event ended
/// what it follows (a run's end), or when stopped.
actor EventFeed {
    typealias Opener = @Sendable (_ after: Int) -> URLRequest

    private let open: Opener
    private let session: URLSession
    private let isFinal: @Sendable (EventRecord) -> Bool
    private let wantsEvents: Bool
    private var lastId: Int
    private var attemptTask: Task<Bool, Never>?
    private var waitTask: Task<Void, Never>?
    private var kicked: Bool = false
    private var stopped: Bool = false
    private var failures: Int = 0
    private var kickedAt: ContinuousClock.Instant?
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "feed")

    /// `wantsEvents` false is the lifecycle stream: its messages are not event records of one run, they are told as
    /// notices (`event` name) for the reader to refetch what it shows.
    init(
        session: URLSession, after: Int, wantsEvents: Bool = true, isFinal: @escaping @Sendable (EventRecord) -> Bool = { _ in false },
        open: @escaping Opener
    ) {
        self.session = session
        self.open = open
        self.lastId = after
        self.isFinal = isFinal
        self.wantsEvents = wantsEvents
    }

    var lastEventId: Int { lastId }

    /// Starts the feed; the stream ends when the feed does.
    func start() -> AsyncStream<FeedUpdate> {
        let (stream, continuation): (AsyncStream<FeedUpdate>, AsyncStream<FeedUpdate>.Continuation) = AsyncStream.makeStream(
            of: FeedUpdate.self, bufferingPolicy: .unbounded)
        let loop: Task<Void, Never> = Task { [weak self] in
            await self?.loop(continuation)
            continuation.finish()
        }
        continuation.onTermination = { _ in loop.cancel() }
        return stream
    }

    /// Reconnects now (the app came to the front, the network changed): no wait, no new attempt count.
    func kick() {
        kickedAt = ContinuousClock.now
        kicked = true
        failures = 0
        waitTask?.cancel()
        attemptTask?.cancel()
    }

    func stop() {
        stopped = true
        waitTask?.cancel()
        attemptTask?.cancel()
    }

    private func loop(_ out: AsyncStream<FeedUpdate>.Continuation) async {
        while !stopped && !Task.isCancelled {
            kicked = false
            out.yield(.state(.connecting))
            let request: URLRequest = open(lastId)
            let attempt: Task<Bool, Never> = Task { [session, isFinal, wantsEvents] in
                await Self.connect(
                    request: request, session: session, wantsEvents: wantsEvents, isFinal: isFinal, out: out,
                    onLive: { [weak self] in await self?.live() },
                    onEvents: { [weak self] top in await self?.read(top) },
                    onFailure: { [weak self] error in await self?.failed(error) })
            }
            attemptTask = attempt
            let done: Bool = await attempt.value
            attemptTask = nil
            if done || stopped { break }
            if kicked { continue }
            failures += 1
            let unauthorized: Bool = lastFailureWasAuth
            if unauthorized {
                // The daemon refused the phone's key: more tries will not mend it, pairing again will.
                out.yield(.state(.reconnecting(attempt: failures, unauthorized: true)))
                break
            }
            out.yield(.state(.reconnecting(attempt: failures, unauthorized: unauthorized)))
            let wait: Task<Void, Never> = Task { try? await Task.sleep(for: backoffDelay(attempt: failures)) }
            waitTask = wait
            await wait.value
            waitTask = nil
        }
        out.yield(.state(.ended))
    }

    private var lastFailureWasAuth: Bool = false

    /// The stream answered: when a kick asked for it, how long that took.
    private func live() {
        if let kickedAt {
            let took: Duration = kickedAt.duration(to: .now)
            logger.info("reconnected \(took.components.seconds * 1000 + took.components.attoseconds / 1_000_000_000_000_000, privacy: .public) ms after the kick")
            self.kickedAt = nil
        }
    }

    private func read(_ top: Int) { lastId = max(lastId, top); failures = 0 }

    private func failed(_ error: any Error) {
        if case DaemonRequestError.status(_, let code, _) = error { lastFailureWasAuth = code == 401 || code == 403 } else { lastFailureWasAuth = false }
        logger.info("feed dropped: \(String(describing: error), privacy: .public)")
    }

    /// One connection, until it ends. True when the feed is over (an event said so); false when the stream ended or
    /// failed and the feed should try again.
    private static func connect(
        request: URLRequest, session: URLSession, wantsEvents: Bool, isFinal: @Sendable (EventRecord) -> Bool,
        out: AsyncStream<FeedUpdate>.Continuation, onLive: @Sendable () async -> Void, onEvents: @Sendable (Int) async -> Void, onFailure: @Sendable (any Error) async -> Void
    ) async -> Bool {
        do {
            let (bytes, response): (URLSession.AsyncBytes, URLResponse) = try await session.bytes(for: request)
            guard let http: HTTPURLResponse = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            guard http.statusCode == 200 else {
                throw DaemonRequestError.status(path: request.url?.path ?? "", code: http.statusCode, body: "")
            }
            out.yield(.state(.live))
            await onLive()
            var parser: SSEParser = SSEParser()
            var line: [UInt8] = []
            for try await byte in bytes {
                line.append(byte)
                guard byte == 0x0A else { continue }
                let messages: [SSEMessage] = parser.feed(line)
                line.removeAll(keepingCapacity: true)
                if messages.isEmpty { continue }
                var events: [EventRecord] = []
                var finished: Bool = false
                for message in messages {
                    if !wantsEvents || message.event == "resync" {
                        out.yield(.notice(message.event ?? "message"))
                        continue
                    }
                    let event: EventRecord
                    do {
                        event = try JSONDecoder().decode(EventRecord.self, from: Data(message.data.utf8))
                    } catch {
                        // The stream goes on (the next events are worth more than a stop), but the screen is told, and the
                        // log keeps which event it was.
                        Logger(subsystem: "dev.dotagent.phone", category: "feed").error(
                            "an event could not be read: id=\(message.id ?? "none", privacy: .public) kind=\(message.event ?? "none", privacy: .public) error=\(String(describing: error), privacy: .public)")
                        out.yield(.notice("A message of this run could not be read. Reopen the chat to read it again."))
                        continue
                    }
                    events.append(event)
                    if isFinal(event) { finished = true }
                }
                if !events.isEmpty {
                    await onEvents(events.map(\.id).max() ?? 0)
                    out.yield(.events(events))
                }
                if wantsEvents == false { await onEvents(0) }
                if finished { return true }
            }
            // The server closed the stream: for a run's stream that is its end only when an event said so.
            return false
        } catch is CancellationError {
            return false
        } catch {
            await onFailure(error)
            return false
        }
    }
}
