import Foundation
import os

// Dot's asks to this phone (item 2051): its calendars, reminders and location, done on the phone and answered.

extension DaemonAPI {
    /// The longest the daemon holds `GET /api/phone/asks/next` open with no ask (`POLL_S`, 20 s), with room for the link.
    static let askWait: Double = 35

    /// The next ask for this phone, or `nil` when the daemon had none in its wait (204). Only a phone's key may.
    public func nextAsk() async throws(DaemonRequestError) -> PhoneAsk? {
        let path: String = "/api/phone/asks/next"
        var built: URLRequest = request(method: "GET", path: path, query: [], body: nil)
        built.timeoutInterval = Self.askWait
        let data: Data
        let response: URLResponse
        Traffic.shared.requested(sending: 0)
        do {
            // The stream session: the request session gives up after 10 s of silence, and this one waits 20.
            (data, response) = try await streamSession.data(for: built)
        } catch let error as URLError {
            throw .transport(path: path, underlying: error)
        } catch {
            throw .transport(path: path, underlying: URLError(.unknown))
        }
        guard let http: HTTPURLResponse = response as? HTTPURLResponse else {
            throw .transport(path: path, underlying: URLError(.badServerResponse))
        }
        Traffic.shared.received(data.count)
        if http.statusCode == 204 { return nil }
        guard (200..<300).contains(http.statusCode) else {
            throw .status(path: path, code: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
        return try decode(data, path: path)
    }

    /// The answer to the ask `id`; 404 when the run that asked gave up waiting.
    public func answerAsk(_ id: String, answer: PhoneAnswer) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/phone/asks/\(id)", body: Self.json(PhoneAnswerBody(answer: answer)))
    }
}

/// What does an ask on the phone: the app's EventKit and Core Location, a stand-in in the tests. It never throws: what
/// it cannot do is a `.refused` answer, which the run reads.
public protocol PhoneAskHandler: Sendable {
    func answer(_ ask: PhoneAsk) async -> PhoneAnswer
}

/// Holds `GET /api/phone/asks/next` open while the app is in front and hands each ask to the handler, each in a task
/// of its own so that an ask waiting on the owner's yes to a permission does not keep the next one from coming.
/// `start` on coming to the front, `stop` on leaving it: iOS lets nothing through from the background, and the daemon
/// tells a run so at once while no request of this phone waits.
public actor PhoneAskLoop {
    private let api: DaemonAPI
    private let handler: any PhoneAskHandler
    private let clock: any EngineClock
    private var loop: Task<Void, Never>?
    private var answering: [String: Task<Void, Never>] = [:]
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "asks")

    public init(api: DaemonAPI, handler: any PhoneAskHandler, clock: any EngineClock) {
        self.api = api
        self.handler = handler
        self.clock = clock
    }

    public var running: Bool { loop != nil }

    public func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in await self?.poll() }
    }

    /// Ends the waiting request; an ask already being done is finished and answered while iOS lets it.
    public func stop() {
        loop?.cancel()
        loop = nil
    }

    private func poll() async {
        var attempt: Int = 0
        while !Task.isCancelled {
            do {
                if let ask: PhoneAsk = try await api.nextAsk() { take(ask) }
                attempt = 0
            } catch {
                if Task.isCancelled { return }
                if let code: Int = error.statusCode, [401, 403, 404].contains(code) {
                    // The key is refused, or the daemon is older than item 2051: nothing to ask again until the app starts again.
                    logger.warning("asks stopped path=/api/phone/asks/next status=\(code, privacy: .public)")
                    loop = nil
                    return
                }
                attempt += 1
                logger.warning("asks request failed attempt=\(attempt, privacy: .public) error=\(error.description, privacy: .public)")
                do {
                    try await clock.sleep(seconds: reconnectWait(attempt: attempt, jitter: 0.5))
                } catch {
                    return
                }
            }
        }
    }

    private func take(_ ask: PhoneAsk) {
        let id: String = ask.id
        answering[id] = Task { [api, handler, logger, weak self] in
            let answer: PhoneAnswer = await handler.answer(ask)
            do {
                try await api.answerAsk(id, answer: answer)
            } catch {
                logger.warning("an answer was not taken ask=\(id, privacy: .public) error=\(String(describing: error), privacy: .public)")
            }
            await self?.answered(id)
        }
    }

    private func answered(_ id: String) { answering[id] = nil }

    /// The asks taken and not answered yet (tests).
    public var unanswered: Int { answering.count }
}
