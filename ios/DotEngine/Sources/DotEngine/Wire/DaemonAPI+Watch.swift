import Foundation

// Watching from the phone (items 1837-1839): the Mac's screen, a run's browser cast, and the takeover of a run's page.

extension DaemonAPI {
    /// The Mac's screen as H.264 frames, in chunks as the network hands them (`GET /api/screen/live?quality=`); read
    /// them with `splitLiveFrames`. A Mac without Screen Recording answers 4xx/5xx with its reason.
    public func screenLive(quality: WatchQuality) -> AsyncThrowingStream<Data, any Error> {
        chunks(path: "/api/screen/live", query: [URLQueryItem(name: "quality", value: quality.rawValue)], accept: "application/octet-stream")
    }

    /// The run's browser tab as Chrome paints it, in chunks of its event stream (`GET /api/runs/{id}/screencast`); read
    /// them with `SSEParser` and `castFrame(of:)`. Ends when the run ends.
    public func screencast(run: String) -> AsyncThrowingStream<Data, any Error> {
        chunks(path: "/api/runs/\(run)/screencast", query: [], accept: "text/event-stream")
    }

    /// Holds the run at its next step for the owner to use its page from this phone (`/takeover/screen`, item 470):
    /// nothing comes to the front of the Mac. 409 when `[browser] remote_input` is off or the run is not active.
    public func takeOverOnScreen(run: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/runs/\(run)/takeover/screen", body: nil)
    }

    /// Gives the run back: it goes on and is told the page may have changed.
    public func handBack(run: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/runs/\(run)/handback", body: nil)
    }

    /// The owner's taps, scrolls and keys on the held run's page, in order (at most `remoteBatchLimit` events).
    public func sendInput(run: String, events: [RemoteInputEvent]) async throws(DaemonRequestError) -> RemoteInputResult {
        try await send("POST", "/api/runs/\(run)/input", body: Self.json(RemoteInputBatch(events: events)))
    }

    /// A long answer read in the chunks the network hands over, on a session of its own made from the stream session's
    /// configuration (so a test's stand-in daemon answers it too). Cancelling the reader ends the request. A status other
    /// than 2xx is thrown as `DaemonRequestError.status` with the start of its body.
    func chunks(path: String, query: [URLQueryItem], accept: String) -> AsyncThrowingStream<Data, any Error> {
        var built: URLRequest = request(method: "GET", path: path, query: query, body: nil)
        built.setValue(accept, forHTTPHeaderField: "Accept")
        built.timeoutInterval = 3600
        let configuration: URLSessionConfiguration = streamSession.configuration
        return AsyncThrowingStream(Data.self, bufferingPolicy: .unbounded) { continuation in
            let receiver: ChunkReceiver = ChunkReceiver(path: path, continuation: continuation)
            let session: URLSession = URLSession(configuration: configuration, delegate: receiver, delegateQueue: nil)
            let task: URLSessionDataTask = session.dataTask(with: built)
            continuation.onTermination = { _ in
                task.cancel()
                session.invalidateAndCancel()
            }
            task.resume()
        }
    }
}

/// Hands a data task's chunks to a stream, and its end or failure. The delegate of one session, called on its queue.
final class ChunkReceiver: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let path: String
    private let continuation: AsyncThrowingStream<Data, any Error>.Continuation
    // Set on the session's serial delegate queue only.
    private var refusedCode: Int?
    private var refusedBody: Data = Data()

    init(path: String, continuation: AsyncThrowingStream<Data, any Error>.Continuation) {
        self.path = path
        self.continuation = continuation
    }

    func urlSession(
        _ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
        completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void
    ) {
        if let http: HTTPURLResponse = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            refusedCode = http.statusCode
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if refusedCode != nil {
            if refusedBody.count < 2000 { refusedBody.append(data.prefix(2000 - refusedBody.count)) }
            return
        }
        continuation.yield(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        if let refusedCode {
            continuation.finish(throwing: DaemonRequestError.status(path: path, code: refusedCode, body: String(decoding: refusedBody, as: UTF8.self)))
        } else if let error = error as? URLError {
            continuation.finish(throwing: error.code == .cancelled ? nil : DaemonRequestError.transport(path: path, underlying: error))
        } else if error != nil {
            continuation.finish(throwing: DaemonRequestError.transport(path: path, underlying: URLError(.unknown)))
        } else {
            continuation.finish()
        }
        session.finishTasksAndInvalidate()
    }
}
